"""
This demo demonstrates how to draw a dynamic mpl (matplotlib) 
plot in a wxPython application.

It allows "live" plotting as well as manual zooming to specific
regions.

Both X and Y axes allow "auto" or "manual" settings. For Y, auto
mode sets the scaling of the graph to see all the data points.
For X, auto mode makes the graph "follow" the data. Set it X min
to manual 0 to always see the whole data from the beginning.

Note: press Enter in the 'manual' text box to make a new value 
affect the plot.

Eli Bendersky (eliben@gmail.com)
License: this code is in the public domain
Last modified: 31.07.2008
"""
import os
import pprint
import random
import sys
import wx
import socket
import time
import threading
import select
import re

# The recommended way to use wx with mpl is with the WXAgg
# backend. 
#
import matplotlib
matplotlib.use('WXAgg')
from matplotlib.figure import Figure
from matplotlib.backends.backend_wxagg import \
    FigureCanvasWxAgg as FigCanvas, \
    NavigationToolbar2WxAgg as NavigationToolbar
import numpy as np
import pylab

connectionState = 0
dataBuffer = ""
EVENT_DISCONNECTED = wx.NewEventType()
EVENT_NEW_ACC_DATA = wx.NewEventType()


class SocketClientThread(threading.Thread):
    def __init__(self, conn, parent):
        threading.Thread.__init__(self)
        self.conn = conn
        self.parent = parent
        self.command = 0
        self.command_lock = threading.RLock()
        self.command_queue = []
        self.accRegex = "[-+]?\d*\.\d+|\d+"
        self.data_acc_x_array = [0.0]
        self.data_acc_y_array = [0.0]
        self.data_acc_z_array = [0.0]

    def run(self):
        global connectionState, dataBuffer
        while connectionState:
            # this will try to recieve data continuously
            try:
                # this makes sure there's someting to read from the server before actually reading it
                # it's necessary because without it will keep blocking even with a closed connection
                if select.select([self.conn], [], []):
                    # this is blocking
                    data = self.conn.recv(1024)
                
                # connection lost
                if not data:
                    wx.PostEvent(self.parent, wx.PyCommandEvent(EVENT_DISCONNECTED, -1))
                    return
                
                # remove all whitespace characters on the right side
                # append so that if there's a new packet before the previous is shown it doesn't get lost
                dataBuffer += data.rstrip()
                self.command_lock.acquire()
                if self.command == 1:
                    print "sensor started"
                elif self.command == 2 or self.command == 3:
                    acc_data = re.findall(self.accRegex, dataBuffer)
                    if acc_data is not None:
                        #tracing acc_data
                        #print dataBuffer
                        print acc_data
                        
                        self.data_acc_x_array.append(float(acc_data[0]))
                        self.data_acc_y_array.append(float(acc_data[1]))
                        self.data_acc_z_array.append(float(acc_data[2]))
                        
                        if len(self.data_acc_x_array) > 2000:
                        	self.data_acc_x_array.pop(0)
                        	self.data_acc_y_array.pop(0)
                        	self.data_acc_z_array.pop(0)
                        	
                        wx.PostEvent(self.parent, wx.PyCommandEvent(EVENT_NEW_ACC_DATA, -1))
                
                if self.command == 3:
                    self.command_queue.append(3)
                    
                if len(self.command_queue) != 0:
                    self.command = self.command_queue.pop(0)
                    if self.command == 1:
                        self.conn.send("sensor start\n")
                    elif self.command == 2 or self.command == 3:
                        self.conn.send("sensor linearacc\n")
                                    
                self.command_lock.release()
                dataBuffer = "" 
                
            except socket.timeout, e:
                #print e.args[0]
                continue
            except Exception as e:
                #print e.args[0]
                return

    def send(self, data, auto=False):
        # new line defines an end of command on the server side
        command = -1
        self.command_lock.acquire()
        
        if "sensor start" in data:
            command = 1
        elif "sensor linearacc" in data:
            if auto:
                command = 3
            else:
                command = 2
        else:
            print "sending unknown command"
            command = -1
        
        self.command = command
        if len(self.command_queue) != 0:
            self.command_queue.append(command)
        else:
            self.conn.send(data + "\n")
            
        self.command_lock.release()

    def close(self):
        global connectionState
        connectionState = 0
        self.conn.close()


class BoundControlBox(wx.Panel):
    """ A static box with a couple of radio buttons and a text
        box. Allows to switch between an automatic mode and a 
        manual mode with an associated value.
    """
    def __init__(self, parent, ID, label, initval):
        wx.Panel.__init__(self, parent, ID)
        
        self.value = initval
        
        box = wx.StaticBox(self, -1, label)
        sizer = wx.StaticBoxSizer(box, wx.VERTICAL)
        
        self.radio_auto = wx.RadioButton(self, -1, 
            label="Auto", style=wx.RB_GROUP)
        self.radio_manual = wx.RadioButton(self, -1,
            label="Manual")
        self.manual_text = wx.TextCtrl(self, -1, 
            size=(35,-1),
            value=str(initval),
            style=wx.TE_PROCESS_ENTER)
        
        self.Bind(wx.EVT_UPDATE_UI, self.on_update_manual_text, self.manual_text)
        self.Bind(wx.EVT_TEXT_ENTER, self.on_text_enter, self.manual_text)
        
        manual_box = wx.BoxSizer(wx.HORIZONTAL)
        manual_box.Add(self.radio_manual, flag=wx.ALIGN_CENTER_VERTICAL)
        manual_box.Add(self.manual_text, flag=wx.ALIGN_CENTER_VERTICAL)
        
        sizer.Add(self.radio_auto, 0, wx.ALL, 10)
        sizer.Add(manual_box, 0, wx.ALL, 10)
        
        self.SetSizer(sizer)
        sizer.Fit(self)
    
    def on_update_manual_text(self, event):
        self.manual_text.Enable(self.radio_manual.GetValue())
    
    def on_text_enter(self, event):
        self.value = self.manual_text.GetValue()
    
    def is_auto(self):
        return self.radio_auto.GetValue()
        
    def manual_value(self):
        return self.value


class GraphFrame(wx.Frame):
    """ The main frame of the application
    """
    title = 'Demo: dynamic matplotlib graph'
    
    def __init__(self):
        wx.Frame.__init__(self, None, -1, self.title)
        
        self.paused = False
        self.sct = None
        
        self.create_menu()
        self.create_status_bar()
        self.create_main_panel()
        
        self.redraw_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_redraw_timer, self.redraw_timer)        
        self.redraw_timer.Start(100)

    def create_menu(self):
        self.menubar = wx.MenuBar()
        
        menu_file = wx.Menu()
        m_expt = menu_file.Append(-1, "&Save plot\tCtrl-S", "Save plot to file")
        self.Bind(wx.EVT_MENU, self.on_save_plot, m_expt)
        menu_file.AppendSeparator()
        m_exit = menu_file.Append(-1, "E&xit\tCtrl-X", "Exit")
        self.Bind(wx.EVT_MENU, self.on_exit, m_exit)
                
        self.menubar.Append(menu_file, "&File")
        self.SetMenuBar(self.menubar)

    def create_main_panel(self):
        self.panel = wx.Panel(self)

        self.init_plot()
        self.canvas = FigCanvas(self.panel, -1, self.fig)

        self.xmin_control = BoundControlBox(self.panel, -1, "X min", 0)
        self.xmax_control = BoundControlBox(self.panel, -1, "X max", 50)
        self.ymin_control = BoundControlBox(self.panel, -1, "Y min", 0)
        self.ymax_control = BoundControlBox(self.panel, -1, "Y max", 100)
        
        self.pause_button = wx.Button(self.panel, -1, "Pause")
        self.Bind(wx.EVT_BUTTON, self.on_pause_button, self.pause_button)
        self.Bind(wx.EVT_UPDATE_UI, self.on_update_pause_button, self.pause_button)
        
        self.cb_grid = wx.CheckBox(self.panel, -1, 
            "Show Grid",
            style=wx.ALIGN_RIGHT)
        self.Bind(wx.EVT_CHECKBOX, self.on_cb_grid, self.cb_grid)
        self.cb_grid.SetValue(True)
        
        self.cb_xlab = wx.CheckBox(self.panel, -1, 
            "Show X labels",
            style=wx.ALIGN_RIGHT)
        self.Bind(wx.EVT_CHECKBOX, self.on_cb_xlab, self.cb_xlab)        
        self.cb_xlab.SetValue(True)
        
        #set tcp ip control box
        self.tcp_ip = wx.TextCtrl(self.panel, size=(120, -1), value="192.168.0.105")
        self.tcp_port = wx.TextCtrl(self.panel, value="8888")
        self.button_connect = wx.Button(self.panel, label="Connect")
        self.track_acc = wx.Button(self.panel, label="TrackAcc")
        self.track_acc.Enable(False)
        
        self.hbox0 = wx.BoxSizer(wx.HORIZONTAL)
        self.hbox0.Add(wx.StaticText(self.panel, label="IP:"), flag=wx.TOP|wx.LEFT, border=5)
        self.hbox0.Add(self.tcp_ip, flag=wx.EXPAND|wx.LEFT, border=5)
        self.hbox0.AddSpacer(20)
        self.hbox0.Add(wx.StaticText(self.panel, label="Port:"), flag=wx.TOP, border=5)
        self.hbox0.Add(self.tcp_port, flag=wx.EXPAND)
        self.hbox0.AddSpacer(20)
        self.hbox0.Add(self.button_connect, flag=wx.EXPAND|wx.RIGHT, border=5)
        self.hbox0.AddSpacer(20)
        self.hbox0.Add(self.track_acc, flag=wx.EXPAND|wx.RIGHT, border=5)
        self.track_acc.Bind(wx.EVT_BUTTON, self.func_track_acc)
        self.button_connect.Bind(wx.EVT_BUTTON, self.connect)
        
        self.hbox1 = wx.BoxSizer(wx.HORIZONTAL)
        self.hbox1.Add(self.pause_button, border=5, flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL)
        self.hbox1.AddSpacer(20)
        self.hbox1.Add(self.cb_grid, border=5, flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL)
        self.hbox1.AddSpacer(10)
        self.hbox1.Add(self.cb_xlab, border=5, flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL)
        
        self.hbox2 = wx.BoxSizer(wx.HORIZONTAL)
        self.hbox2.Add(self.xmin_control, border=5, flag=wx.ALL)
        self.hbox2.Add(self.xmax_control, border=5, flag=wx.ALL)
        self.hbox2.AddSpacer(24)
        self.hbox2.Add(self.ymin_control, border=5, flag=wx.ALL)
        self.hbox2.Add(self.ymax_control, border=5, flag=wx.ALL)
        
        self.vbox = wx.BoxSizer(wx.VERTICAL)
        self.vbox.Add(self.canvas, 1, flag=wx.LEFT | wx.TOP | wx.GROW)
        self.vbox.Add(self.hbox0, 0, flag=wx.ALIGN_LEFT | wx.TOP)        
        self.vbox.Add(self.hbox1, 0, flag=wx.ALIGN_LEFT | wx.TOP)
        self.vbox.Add(self.hbox2, 0, flag=wx.ALIGN_LEFT | wx.TOP)
        
        self.panel.SetSizer(self.vbox)
        self.vbox.Fit(self)
    
    def create_status_bar(self):
        self.statusbar = self.CreateStatusBar()
        
    def func_track_acc(self, event):
        global connectionState

        if connectionState:
            #check if sensor service has already started
            self.sct.send("sensor linearacc", True)
        
    def connect(self, event):
        global connectionState
        
        # disconnect when a socket is connected
        if connectionState:
            self.disconnect(wx.EVT_BUTTON)
            return
        
        self.clientSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # set timeout in case a wrong ip is used (in seconds)
        self.clientSocket.settimeout(2)
        ip = self.tcp_ip.GetValue()
        port = int(self.tcp_port.GetValue())
        
        try:
            self.clientSocket.connect((ip, port))
        except socket.timeout:
            wx.MessageBox('Timeout while connecting, verify the IP and port.', 'Error', wx.OK|wx.ICON_ERROR)
            return
        except Exception, e:
            wx.MessageBox('Could not connect to socket, is the app running?', 'Error', wx.OK|wx.ICON_ERROR)
            return
        
        # start the receieveing thread
        connectionState = 1
        self.sct = SocketClientThread(self.clientSocket, self)
        self.sct.start()
        self.sct.send("sensor start")
        
        # update UI element statuses
        self.button_connect.SetLabel("Stop")
        self.tcp_ip.Enable(False)
        self.tcp_port.Enable(False)
        self.track_acc.Enable(True)        

        # set focus on the send field
        wx.Window.SetFocus(self.track_acc)
        
    def disconnect(self, event):
        global connectionState
        self.clientSocket.close()
        connectionState = 0
        
        if event != wx.EVT_BUTTON:
            wx.MessageBox('Connection lost!', 'Error', wx.OK|wx.ICON_ERROR)
        
        # update UI element statuses
        self.button_connect.SetLabel("Connect")
        self.tcp_ip.Enable(True)
        self.tcp_port.Enable(True)
        self.track_acc.Enable(False)
        
        # set focus on the ip field
        wx.Window.SetFocus(self.tcp_ip)        
        
    def init_plot(self):
        self.dpi = 100
        self.fig = Figure(None, self.dpi)
        self.fig.subplots_adjust(bottom=0.05, wspace=0.1, hspace=0.2, left=0.0455, top=0.99, right=0.99)
        self.axes = self.fig.add_subplot(311)
#        self.axes.set_axis_bgcolor('black')
#        self.axes.set_title('Acc x', size=12)
       
        self.axes_y = self.fig.add_subplot(312)
#        self.axes_y.set_axis_bgcolor('black')
#        self.axes_y.set_title('Acc y', size=12)
        
        self.axes_z = self.fig.add_subplot(313)
#        self.axes_z.set_axis_bgcolor('black')

        pylab.setp(self.axes.get_xticklabels(), fontsize=8)
        pylab.setp(self.axes.get_yticklabels(), fontsize=8)
        
        pylab.setp(self.axes_y.get_xticklabels(), fontsize=8)
        pylab.setp(self.axes_y.get_yticklabels(), fontsize=8)
        
        pylab.setp(self.axes_z.get_xticklabels(), fontsize=8)
        pylab.setp(self.axes_z.get_yticklabels(), fontsize=8)

        # plot the data as a line series, and save the reference 
        # to the plotted line series
        #
        array = [0]
        if self.sct is not None:
            array = self.sct.data_acc_x_array
        self.plot_data = self.axes.plot(
            array, 
            linewidth=1,
            color=(1, 1, 0),
            )[0]
            
        array = [0]
        if self.sct is not None:
            array = self.sct.data_acc_y_array
        self.plot_data_y = self.axes_y.plot(
            array, 
            linewidth=1,
            color=(1, 1, 0),
            )[0]
            
        array = [0]
        if self.sct is not None:
            array = self.sct.data_acc_z_array
        self.plot_data_z = self.axes_z.plot(
            array, 
            linewidth=1,
            color=(1, 1, 0),
            )[0]

    def draw_plot(self):
        """ Redraws the plot
        """
        # when xmin is on auto, it "follows" xmax to produce a 
        # sliding window effect. therefore, xmin is assigned after
        # xmax.
        #
        array = [0]
        if self.sct is not None:
            array = self.sct.data_acc_x_array
            
        array_y = [0]
        if self.sct is not None:
            array_y = self.sct.data_acc_y_array
            
        array_z = [0]
        if self.sct is not None:
            array_z = self.sct.data_acc_z_array
        
        # x
        if self.xmax_control.is_auto():
            xmax = len(array) if len(array) > 20 else 20
        else:
            xmax = int(self.xmax_control.manual_value())
            
        if self.xmin_control.is_auto():            
            xmin = xmax - 20
        else:
            xmin = int(self.xmin_control.manual_value())

        # for ymin and ymax, find the minimal and maximal values
        # in the data set and add a mininal margin.
        # 
        # note that it's easy to change this scheme to the 
        # minimal/maximal value in the current display, and not
        # the whole data set.
        # 
        if self.ymin_control.is_auto():
            ymin = round(min(array), 0) - 1
        else:
            ymin = int(self.ymin_control.manual_value())
        
        if self.ymax_control.is_auto():
            ymax = round(max(array), 0) + 1
        else:
            ymax = int(self.ymax_control.manual_value())

        self.axes.set_xbound(lower=xmin, upper=xmax)
        self.axes.set_ybound(lower=ymin, upper=ymax)
        
        # y 
        if self.xmax_control.is_auto():
            xmax = len(array_y) if len(array_y) > 20 else 20
        else:
            xmax = int(self.xmax_control.manual_value())
            
        if self.xmin_control.is_auto():            
            xmin = xmax - 20
        else:
            xmin = int(self.xmin_control.manual_value())

        # for ymin and ymax, find the minimal and maximal values
        # in the data set and add a mininal margin.
        # 
        # note that it's easy to change this scheme to the 
        # minimal/maximal value in the current display, and not
        # the whole data set.
        # 
        if self.ymin_control.is_auto():
            ymin = round(min(array_y), 0) - 1
        else:
            ymin = int(self.ymin_control.manual_value())
        
        if self.ymax_control.is_auto():
            ymax = round(max(array_y), 0) + 1
        else:
            ymax = int(self.ymax_control.manual_value())

        self.axes_y.set_xbound(lower=xmin, upper=xmax)
        self.axes_y.set_ybound(lower=ymin, upper=ymax)
        
        # z 
        if self.xmax_control.is_auto():
            xmax = len(array_z) if len(array_z) > 20 else 20
        else:
            xmax = int(self.xmax_control.manual_value())
            
        if self.xmin_control.is_auto():            
            xmin = xmax - 20
        else:
            xmin = int(self.xmin_control.manual_value())

        # for ymin and ymax, find the minimal and maximal values
        # in the data set and add a mininal margin.
        # 
        # note that it's easy to change this scheme to the 
        # minimal/maximal value in the current display, and not
        # the whole data set.
        # 
        if self.ymin_control.is_auto():
            ymin = round(min(array_z), 0) - 1
        else:
            ymin = int(self.ymin_control.manual_value())
        
        if self.ymax_control.is_auto():
            ymax = round(max(array_z), 0) + 1
        else:
            ymax = int(self.ymax_control.manual_value())

        self.axes_z.set_xbound(lower=xmin, upper=xmax)
        self.axes_z.set_ybound(lower=ymin, upper=ymax)
                
        # anecdote: axes.grid assumes b=True if any other flag is
        # given even if b is set to False.
        # so just passing the flag into the first statement won't
        # work.
        #
        if self.cb_grid.IsChecked():
            self.axes.grid(True, color='gray')
            self.axes_y.grid(True, color='gray')
            self.axes_z.grid(True, color='gray')
        else:
            self.axes.grid(False)
            self.axes_y.grid(False)
            self.axes_z.grid(False)

        # Using setp here is convenient, because get_xticklabels
        # returns a list over which one needs to explicitly 
        # iterate, and setp already handles this.
        #  
        pylab.setp(self.axes.get_xticklabels(), 
            visible=self.cb_xlab.IsChecked())
            
        pylab.setp(self.axes_y.get_xticklabels(), 
            visible=self.cb_xlab.IsChecked())
            
        pylab.setp(self.axes_z.get_xticklabels(), 
            visible=self.cb_xlab.IsChecked())
        
        self.plot_data.set_xdata(np.arange(len(array)))
        self.plot_data.set_ydata(np.array(array))
        
        self.plot_data_y.set_xdata(np.arange(len(array_y)))
        self.plot_data_y.set_ydata(np.array(array_y))
        
        self.plot_data_z.set_xdata(np.arange(len(array_z)))
        self.plot_data_z.set_ydata(np.array(array_z))
        
        self.canvas.draw()
    
    def on_pause_button(self, event):
        self.paused = not self.paused
    
    def on_update_pause_button(self, event):
        label = "Resume" if self.paused else "Pause"
        self.pause_button.SetLabel(label)
    
    def on_cb_grid(self, event):
        self.draw_plot()
    
    def on_cb_xlab(self, event):
        self.draw_plot()
    
    def on_save_plot(self, event):
        file_choices = "PNG (*.png)|*.png"
        
        dlg = wx.FileDialog(
            self, 
            message="Save plot as...",
            defaultDir=os.getcwd(),
            defaultFile="plot.png",
            wildcard=file_choices,
            style=wx.SAVE)
        
        if dlg.ShowModal() == wx.ID_OK:
            path = dlg.GetPath()
            self.canvas.print_figure(path, dpi=self.dpi)
            self.flash_status_message("Saved to %s" % path)
    
    def on_redraw_timer(self, event):
        # if paused do not add data, but still redraw the plot
        # (to respond to scale modifications, grid change, etc.)
        #
        #if not self.paused:
        #    self.data.append(self.datagen.next())
        
        self.draw_plot()
    
    def on_exit(self, event):
        self.Destroy()
    
    def flash_status_message(self, msg, flash_len_ms=1500):
        self.statusbar.SetStatusText(msg)
        self.timeroff = wx.Timer(self)
        self.Bind(
            wx.EVT_TIMER, 
            self.on_flash_status_off, 
            self.timeroff)
        self.timeroff.Start(flash_len_ms, oneShot=True)
    
    def on_flash_status_off(self, event):
        self.statusbar.SetStatusText('')


if __name__ == '__main__':
    app = wx.PySimpleApp()
    app.frame = GraphFrame()
    app.frame.Show()
    app.MainLoop()


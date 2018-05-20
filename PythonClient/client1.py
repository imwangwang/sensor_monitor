#!/usr/bin/env python2.7
import socket
import time
import threading
import select
import re
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import Slider, Button, RadioButtons
import matplotlib.animation as animation

class SocketClientThread(threading.Thread):
    def __init__(self, conn):
        threading.Thread.__init__(self)
        self.conn = conn

    def run(self):
        global connectionState
        global accRegex,accDataLock
        global acc_ax1_list
        global acc_ax2_list
        global acc_ax3_list

        trackingState = 1

        while connectionState:
            # this will try to recieve data continuously
            try:
                # this makes sure there's someting to read from the server before actually reading it
                # it's necessary because without it will keep blocking even with a closed connection
                if select.select([self.conn], [], [], 0.5):
                    # this is blocking
                    data = self.conn.recv(1024)
                
                # connection lost
                if not data:
                    print "connection is LOST"
                    return
                
                # remove all whitespace characters on the right side
                # append so that if there's a new packet before the previous is shown it doesn't get lost
                dataStrip = data.rstrip()
                if trackingState == 1:
                    if "not started" in dataStrip:
                        self.send("sensor start")
                        trackingState = 2
                elif trackingState == 2:
                    self.send("sensor acc")
                    trackingState = 3
                elif trackingState == 3:
                    accData = re.findall(accRegex, dataStrip)
                    if accData is not None:
                        accDataLock.acquire()
                        acc_ax1_list.append(float(accData[0]))
                        acc_ax1_list.pop(0)
                        acc_ax2_list.append(float(accData[1]))
                        acc_ax2_list.pop(0)
                        acc_ax3_list.append(float(accData[2]))
                        acc_ax3_list.pop(0)
                        accDataLock.release()
                        
                        print accData[0],accData[1],accData[2]

                        self.send("sensor acc")
                    else:
                        print "failure to get acc Data: " + data
                else:
                    print trackingState

            except socket.timeout, e:
                print e.args[0]
                continue
            except Exception as e:
                print e.args[0]
                return

    def send(self, data):
        # new line defines an end of command on the server side
        self.conn.send(data + "\n")

    def close(self):
        global connectionState

        connectionState = False
        self.conn.close()


serverIP = '192.168.31.227'
serverPort = 8888
clientSocket = None
clientTalkSocket = None
connectionState = False
dataBuffer = ""
accDataLock = threading.RLock()

trackingState = 0
# 0: initial, 1: trigger, 2: start, 3: running
accRegex = "[-+]?\d*\.\d+|\d+"

x_coordinate_range = 1000

accDataLock.acquire()
acc_ax1_list = []
acc_ax2_list = []
acc_ax3_list = []
accDataLock.release()

def connect():
    global serverIP
    global serverPort
    global connectionState
    global clientTalkSocket

    clientSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # set timeout in case a wrong ip is used (in seconds)
    clientSocket.settimeout(2)
    ip = serverIP
    port = serverPort
    
    try:
        clientSocket.connect((ip, port))
    except socket.timeout:
        print 'Timeout while connecting, verify the IP and port.'
        return 1
    except Exception, e:
        print 'Could not connect to socket, is the app running?'
        return 1
    
    # start the receieveing thread
    connectionState = True
    clientTalkSocket = SocketClientThread(clientSocket)
    clientTalkSocket.start()
    

def init():
    global acc_ax1_list
    global acc_ax2_list
    global acc_ax3_list
    global accDataLock
    global acc_line,acc_line2,acc_line3
    global x_coordinate_range
   
    accDataLock.acquire()
    acc_ax1_list = [0.0 for i in xrange(0, x_coordinate_range)]
    acc_ax2_list = [0.0 for i in xrange(0, x_coordinate_range)]
    acc_ax3_list = [0.0 for i in xrange(0, x_coordinate_range)]
    accDataLock.release()
    
    acc_line.set_ydata(acc_ax1_list)
    acc_line2.set_ydata(acc_ax2_list)
    acc_line3.set_ydata(acc_ax3_list)
    return acc_line,acc_line2,acc_line3

def update(val):
    global acc_ax1_list
    global acc_ax2_list
    global acc_ax3_list
    global acc_line,acc_line2,acc_line3
    global accDataLock

    print "update:%d,%d,%d"%(len(acc_ax1_list), len(acc_ax2_list), len(acc_ax3_list))
    accDataLock.acquire()       
    acc_line.set_ydata(acc_ax1_list)
    acc_line2.set_ydata(acc_ax2_list)
    acc_line3.set_ydata(acc_ax3_list)
    accDataLock.release()

    return acc_line,acc_line2,acc_line3

def func_track_acc(event):
    global connectionState
    global buttonClicked

    if connectionState and buttonClicked is not True:
        #check if sensor service has already started
        clientTalkSocket.send("sensor acc")
        print "sensor acc command sent\n"
        buttonClicked = True
    else:
        print "button is clicked before"
        

if (connect()) :
    print "connection failure\n"
    exit(1)

axcolor = 'lightgoldenrodyellow'
print "connection established\n"
fig = plt.figure(1)

ax1 = fig.add_subplot(3, 1, 1, xlim=(0, x_coordinate_range), ylim=(-20, 20))
ax2 = fig.add_subplot(3, 1, 2, xlim=(0, x_coordinate_range), ylim=(-20, 20))
ax3 = fig.add_subplot(3, 1, 3, xlim=(0, x_coordinate_range), ylim=(-20, 20))

acc_ax1_list = [0.0 for i in xrange(0, x_coordinate_range)]
acc_ax2_list = [0.0 for i in xrange(0, x_coordinate_range)]
acc_ax3_list = [0.0 for i in xrange(0, x_coordinate_range)]

acc_line, = ax1.plot(acc_ax1_list)
acc_line2, = ax2.plot(acc_ax2_list)
acc_line3, = ax3.plot(acc_ax3_list)

buttonClicked = False
trackax = plt.axes([0.8, 0.025, 0.1, 0.04])
button = Button(trackax, 'Track', color=axcolor, hovercolor='0.975')
button.on_clicked(func_track_acc)

ani = animation.FuncAnimation(fig, update, init_func=init, interval=2*100)
plt.show()
print "quiting..."
clientTalkSocket.close()
clientTalkSocket.join()


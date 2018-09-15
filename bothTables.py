#!/usr/bin/env python

from lxml import html
from lxml import etree
import requests
import sqlite3
import sys
import cgi
import cgitb
import datetime
import time
import subprocess


# global variables
speriod=(15*60)-1
dbname='/var/www/neuriolog.db'
ip='192.168.13.239'
endPoint='/both_tables.html'
myOption=''

# store the energy in the database
def log_energy(net,gen,cons):
    conn=sqlite3.connect(dbname)
    curs=conn.cursor()
    print '2'
    curs.execute('INSERT INTO energy values(?,?,?,?,?,?,?)', (datetime.datetime.now(),net[1],net[2],gen[1],gen[2],cons[1],cons[2]))

    conn.commit()

    conn.close()

def is_number(s):
    try:
        float(s)
        return True
    except ValueError:
        pass
 
    try:
        import unicodedata
        unicodedata.numeric(s)
        return True
    except (TypeError, ValueError):
        pass
    return False
# print the HTTP header
def printHTTPheader():
    print "Content-type: text/html\n\n"



# print the HTML head section
# arguments are the page title and the table for the chart
def printHTMLHead(title, table):
    print "<head>"
    print "    <title>"
    print title
    print "    </title>"
    
    print_graph_script(table)

    print "</head>"

def getTimeMilli(inTime):
   return(time.mktime( inTime.timetuple()) *1000)

def read_html():
   data = []
   interim = []
   page = requests.get('http://' + ip + endPoint)
   parser = etree.HTMLParser();
   tree2 = etree.fromstring(page.text,parser)
   #print etree.tostring(tree2)
   walkAll = tree2.getiterator()
   foundChannel = False;
   count = 0
   for elt in walkAll:
      myText = elt.text;
      if myText == 'Channel':
         foundChannel = True
      if foundChannel & (elt.tag == 'td') & (myText != None) :
         #print elt.text, elt.tag
         interim.append(elt.text)
         count = count +1;
         #print interim
         if count == 6:
            count = 0;
            data.append(interim)
            interim = []
            #print data
   retData = [ ['Name','Import','Export'],
               ['Net',data[2][2],data[2][3]],
               ['Gen',data[3][2],data[3][3]],
               ['Con',data[4][2],data[4][3]] ]
   #print retData
   return retData

   


# return a list of records from the database
def get_data(interval):

    conn=sqlite3.connect(dbname)
    curs=conn.cursor()

    #print interval
    if interval == None or int(interval) == -1:
        curs.execute("SELECT * FROM energy")
    else:
        curs.execute("SELECT * FROM energy WHERE timestamp>datetime('now','-%s hours','localtime')" % interval)

    rows=curs.fetchall()

    conn.close()

    return rows


# convert rows from database into a javascript table
def create_table(rows):
    chart_table=""
    smth=0.4
    smth2=1-smth
    smthh=smth*3600

    old_data = None 
    old_value=0
    old_time=0
    for row in rows[:-1]:
        if old_data != None:
           delta=row[1]-old_data
           aTime=datetime.datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
           dTime=aTime-old_time
           value=delta/dTime.total_seconds()*smthh+old_value*smth2
           if value > 8:
              value=8
           if value < -8:
              value=-8
           #rowstr="[new Date({0}), {1}],\n".format(datetime.datetime.strftime(aTime,"%Y,%m,%d,%H,%M,%S"),str(value))
           rowstr="[new Date({0}), {1}, {2}],\n".format(getTimeMilli(aTime),str(row[1]),str(value))
           chart_table+=rowstr
           old_value=value
        old_data=row[1]
        old_time=datetime.datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")

    row=rows[-1]
    delta=row[1]-old_data
    aTime=datetime.datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
    dTime=aTime-old_time
    value=delta/dTime.total_seconds()*3600*0.1+old_value*0.9
    #rowstr="[new Date({0}), {1}]\n".format(getTimeMilli(aTime),str(value))
    rowstr="[new Date({0}), {1}, {2}]\n".format(getTimeMilli(aTime),str(row[1]),str(value))
    #rowstr="['{0}', {1}]\n".format(str(row[0]),str(value))
    chart_table+=rowstr

    #print chart_table
    return chart_table


# print the javascript to generate the chart
# pass the table generated from the database info
def print_graph_script(table):

    # google chart snippet
        #data.setColumnProperty(1, 'type', 'date');
        #data.setColumnProperty(2, 'type', 'number');
    chart_code="""
    <script type="text/javascript" src="https://www.google.com/jsapi"></script>
    <script type="text/javascript">
      google.load("visualization", "1", {packages:["corechart"]});
      google.setOnLoadCallback(drawChart);
      function drawChart() {
        var data = google.visualization.arrayToDataTable([ ['Time', 'Energy(lhs)', 'Power(rhs)'], %s ]);
        data.setColumnProperty(0,'type','datetime');
        data.setColumnProperty(1,'type','number');
        data.setColumnProperty(2,'type','number');
        var options = {
          title: 'Energy/Power',
          vAxes: { 0: {title: 'KWH'},
                   1: {title: 'KWatts' }},
          hAxis: { title: 'Time',   format: 'M/d/yy HH:mm', gridlines:{ color:'#555555', count: 10}},
          series: {0: {targetAxisIndex:0},
                 1: {targetAxisIndex:1}}
        };
        var chart = new google.visualization.LineChart(document.getElementById('chart_div'));
        chart.draw(data, options);
      }
    </script>"""

    print chart_code % (table)




# print the div that contains the graph
def show_graph():
    print "<h2>Energy(KWH)/Power(KW) Chart</h2>"
    print '<div id="chart_div" style="width: 900px; height: 500px;"></div>'



# connect to the db and show some stats
# argument option is the number of hours
def show_stats(option):


    conn=sqlite3.connect(dbname)
    curs=conn.cursor()

    if option is None or int(option) == -1:
        option = str(240000)

    #curs.execute("SELECT * FROM energy WHERE timestamp>datetime('now','-%s hours','localtime')" % interval)
    curs.execute("SELECT timestamp,max(energy) FROM energy WHERE timestamp>datetime('now','-%s hour','localtime') AND timestamp<=datetime('now','localtime')" % option)
    rowmax=curs.fetchone()
    rowstrmax="{0}&nbsp&nbsp&nbsp{1}KWH".format(str(rowmax[0]),str(rowmax[1]))

#    curs.execute("SELECT timestamp,min(temp) FROM temps WHERE timestamp>datetime('now','-%s hour') AND timestamp<=datetime('now')" % option)
    curs.execute("SELECT timestamp,min(energy) FROM energy WHERE timestamp>datetime('now','-%s hour','localtime') AND timestamp<=datetime('now','localtime')" % option)
    rowmin=curs.fetchone()
    rowstrmin="{0}&nbsp&nbsp&nbsp{1}KWH".format(str(rowmin[0]),str(rowmin[1]))

#    curs.execute("SELECT avg(temp) FROM temps WHERE timestamp>datetime('now','-%s hour') AND timestamp<=datetime('now')" % option)
    curs.execute("SELECT avg(energy) FROM energy WHERE timestamp>datetime('now','-%s hour','localtime') AND timestamp<=datetime('now','localtime')" % option)
    rowavg=curs.fetchone()


    print "<hr>"


    print "<h2>Minumum energy&nbsp</h2>"
    print rowstrmin
    print "<h2>Maximum energy</h2>"
    print rowstrmax
    print "<h2>Average energy</h2>"
    print "%.3f" % rowavg+"KWH"

    print "<hr>"

    print "<h2>In the last hour:</h2>"
    print "<table>"
    print "<tr><td><strong>Date/Time</strong></td><td><strong>energy</strong></td></tr>"

#    rows=curs.execute("SELECT * FROM energy WHERE timestamp>datetime('new','-1 hour') AND timestamp<=datetime('new')")
    rows=curs.execute("SELECT * FROM energy WHERE timestamp>datetime('now','-1 hour','localtime') AND timestamp<=datetime('now','localtime')")
    for row in rows:
        rowstr="<tr><td>{0}&emsp;&emsp;</td><td>{1}KWH</td></tr>".format(str(row[0]),str(row[1]))
        print rowstr
    print "</table>"

    print "<hr>"

    conn.close()




def print_time_selector(option):

    print """<form action="/cgi-bin/both.py" method="POST">
        Show the  logs for  
        <select name="timeinterval">"""


    if option is not None:

        if option == "-1":
            print "<option value=\"-1\" selected=\"selected\">All times</option>"
        else:
            print "<option value=\"-1\">All times</option>"
        #if option == None:
            #print "<option value=\"-1\" selected=\"selected\">All times</option>"
        #else:
            #print "<option value=\"-1\">All times</option>"
        if option == "6":
            print "<option value=\"6\" selected=\"selected\">the last 6 hours</option>"
        else:
            print "<option value=\"6\">the last 6 hours</option>"

        if option == "12":
            print "<option value=\"12\" selected=\"selected\">the last 12 hours</option>"
        else:
            print "<option value=\"12\">the last 12 hours</option>"

        if option == "24":
            print "<option value=\"24\" selected=\"selected\">the last 24 hours</option>"
        else:
            print "<option value=\"24\">the last 24 hours</option>"
        if option == "168":
            print "<option value=\"168\" selected=\"selected\">1 week</option>"
        else:
            print "<option value=\"168\">1 week</option>"

    else:
        print """<option value="-1">All times</option>
            <option value="6">the last 6 hours</option>
            <option value="12">the last 12 hours</option>
            <option value="24" selected="selected">the last 24 hours</option>
            <option value="168">1 week</option>"""

    print """        </select>
        <input type="submit" value="Display">
    </form>"""


# check that the option is valid
# and not an SQL injection
def validate_input(option_str):
    # check that the option string represents a number
    #if option_str == -1:
       #return None
    if is_number(option_str):
        # check that the option is within a specific range
        if int(option_str) > -2 and int(option_str) <= 2000:
            return option_str
        else:
            return None
    else: 
        return None


#return the option passed to the script
def get_option():
    form=cgi.FieldStorage()
    if "timeinterval" in form:
        option = form["timeinterval"].value
        return validate_input (option)
    else:
        return None




# main function
# This is where the program starts 
def main():

    # get options that may have been passed to this script
    option=get_option()

    if option is None:
        option = str(24)

    
    # get data from the database
    records=read_html()
    log_energy(records[1],records[2],records[3])
    
    
    #records=get_data(None)

    # print the HTTP header
    #printHTTPheader()

    #if len(records) != 0:
        # convert the data into a table
        #table=create_table(records)
    #else:
        #print "<h1>Raspberry Pi energy/power Logger " 
        #print myOption
        #print "No data found"
        #print "</h1>"
        #return

    #global myOption
    #myOption=''
    # start printing the page
    #print "<html>"
    # print the head section including the table
    # used by the javascript for the chart
    #printHTMLHead("Raspberry Pi energy/power Logger", table)

    # print the page body
    #print "<body>"
    #print "<h1>Raspberry Pi energy/power Logger " 
    #print myOption
    #print "</h1>"
    #print "<hr>"
    #print_time_selector(option)
    #show_graph()
    #show_stats(option)
    #print "</body>"
    #print "</html>"

    sys.stdout.flush()

if __name__=="__main__":
    main()

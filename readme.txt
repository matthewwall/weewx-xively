xively - weewx extension that sends data to Xively/COSM/Pachube
Copyright 2014 Matthew Wall

Installation instructions:

1) run the installer:

wee_extension --install weewx-xively.tgz

2) enter token and feed id in weewx.conf:

[StdRESTful]
    [[Xively]]
        token = TOKEN
        feed = FEED_ID

3) restart weewx

sudo /etc/init.d/weewx stop
sudo /etc/init.d/weewx start

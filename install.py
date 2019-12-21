# $Id: install.py 1483 2016-04-25 06:53:19Z mwall $
# installer for Xively
# Copyright 2014 Matthew Wall

from setup import ExtensionInstaller

def loader():
    return XivelyInstaller()

class XivelyInstaller(ExtensionInstaller):
    def __init__(self):
        super(XivelyInstaller, self).__init__(
            version="0.8",
            name='xively',
            description='Upload weather data to Xively.',
            author="Matthew Wall",
            author_email="mwall@users.sourceforge.net",
            restful_services='user.xively.Xively',
            config={
                'StdRESTful': {
                    'Xively': {
                        'token': 'INSERT_TOKEN_HERE',
                        'feed': 'INSERT_FEED_HERE'}}},
            files=[('bin/user', ['bin/user/xively.py'])]
            )

import re
# import urllib.parse

class UrlValidator:
    def __init__(self, url: str) -> bool:
        self.url = url.strip()

    def isUrl(self) -> bool:
        # Updated regex to support optional port numbers
        pattern = r'^(https?://)?([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}(:[0-9]+)?(/.*)?$'
        result  = re.match(pattern, self.url) is not None
        return result

    def isSpotify(self) -> bool:
        pattern = r'^(https?://)?(open\.)?spotify\.com/.*$'
        result  = re.match(pattern, self.url) is not None
        return result

    def isInstagram(self) -> bool:
        pattern = r'^(https?://)?(www\.)?instagram\.com/.*$'
        result  = re.match(pattern, self.url) is not None
        return result

    # Match YouTube URLs ['www.youtube.com', 'youtu.be', 'youtube.com', 'youtu.be']
    def isYouTube(self) -> bool:
        pattern = r'^(https?://)?(www\.)?(youtube\.com|youtu\.be)/.*$'
        result  = re.match(pattern, self.url) is not None
        return result

    #Auto detect music platforms such as spotify, soundcloud, apple music, tidal, youtube music
    def isMusicPlatform(self) -> bool:
        music_platforms = [
            r'^(https?://)?(open\.)?spotify\.com/.*$',
            r'^(https?://)?(www\.)?soundcloud\.com/.*$',
            r'^(https?://)?(music\.)?apple\.com/.*$',
            r'^(https?://)?(tidal\.com/).*$',
            r'^(https?://)?(music\.)?youtube\.com/.*$',
            r'^(https?://)?(youtu\.be)/.*$'
        ]
        for pattern in music_platforms:
            if re.match(pattern, self.url):
                return True
        return False

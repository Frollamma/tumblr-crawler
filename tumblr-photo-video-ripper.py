# -*- coding: utf-8 -*-

import os
import sys
import requests
import xmltodict
from six.moves import queue as Queue
from threading import Thread
import re
import json
from bs4 import BeautifulSoup


# Tumblr stuff
MEDIA_POST_TYPES = ["regular", "photo", "video"]    # I don't know what "regular" type is for... Sometimes it contains medias, sometimes not, anyway it is a post from the blog owner (not reblogged)

# Downloads folder
DOWNLOADS_FOLDER = os.path.join(os.getcwd(), "downloads")

# Setting timeout
TIMEOUT = 10

# Retry times
RETRY = 5

# Index of the first post to be crawled (indexes start from 0). This index is included
START_POST_INDEX = 0

# Index of the next post after the last post. This index is excluded
END_POST_INDEX = 3000

# Numbers of photos/videos per page
MEDIA_NUM = 50

# Numbers of downloading threads concurrently
THREADS = 10

# Do you like to dump each post as separate json (otherwise you have to extract from bulk xml files)
# This option is for convenience for terminal users who would like to query e.g. with ./jq (https://stedolan.github.io/jq/)
EACH_POST_AS_SEPARATE_JSON = False


def video_hd_match():
    hd_pattern = re.compile(r'.*"hdUrl":("([^\s,]*)"|false),')

    def match(video_player):
        hd_match = hd_pattern.match(video_player)
        try:
            if hd_match is not None and hd_match.group(1) != 'false':
                return hd_match.group(2).replace('\\', '')
        except:
            return None
    return match


def video_default_match():
    default_pattern = re.compile(r'.*src="(\S*)" ', re.DOTALL)

    def match(video_player):
        default_match = default_pattern.match(video_player)
        if default_match is not None:
            try:
                return default_match.group(1)
            except:
                return None
    return match


class DownloadWorker(Thread):
    def __init__(self, queue, proxies=None):
        Thread.__init__(self)
        self.queue = queue
        self.proxies = proxies
        self._register_regex_match_rules()

    def run(self):
        while True:
            medium_type, post, target_folder = self.queue.get()
            self.download(medium_type, post, target_folder)
            self.queue.task_done()

    def download(self, medium_type, post, target_folder):
        try:
            medium_urls = self._handle_medium_urls(medium_type, post)
            
            print("Try to download medium...")
            if medium_urls:
                # print("No medium url specified|")
                
                for medium_url in medium_urls:
                    self._download(medium_type, medium_url, target_folder)
        except TypeError as e:
            print(e)
            print(f"Didn't download medium, with type {medium_type}")
            pass

    # can register different regex match rules
    def _register_regex_match_rules(self):
        # will iterate all the rules
        # the first matched result will be returned
        self.regex_rules = [video_hd_match(), video_default_match()]

    def _parse_images_from_regular_body(self, regular_body) -> list[str]:
        # IMPR: generalize this for videos
        soup = BeautifulSoup(regular_body, "html.parser")
        imgs = soup.find_all("img")
        srcs = []

        for img in imgs:
            srcset = img["srcset"]
            # print(f"{srcset = }")
            # srcset = srcset.strip().strip(",").strip().split("\n")
            # print(f"{srcset = }")
            # best_quality_image_src = srcset[-1].lstrip().rstrip()
            # print(f"{best_quality_image_src = }")
            # best_quality_image_src = best_quality_image_src.split(" ")[0]
            # print(f"{best_quality_image_src = }")
            best_quality_image_src = srcset.split(" ")[-2]
            print(f"{best_quality_image_src = }")
            srcs.append(best_quality_image_src)
        
        return srcs


    def _handle_medium_urls(self, medium_type, post):
        try:
            if medium_type == "photo":
                return [post["photo-url"][0]["#text"]]

            if medium_type == "video":
                video_player = post["video-player"][1]["#text"]
                for regex_rule in self.regex_rules:
                    matched_url = regex_rule(video_player)
                    if matched_url is not None:
                        return [matched_url]
                else:
                    raise Exception
                
            if medium_type == "regular":
                regular_body = post["regular-body"]
                return self._parse_images_from_regular_body(regular_body)
            
        except Exception as e:
            print(e)
            raise TypeError("Unable to find the right url for downloading. "
                            "Please open a new issue on "
                            "https://github.com/dixudx/tumblr-crawler/"
                            "issues/new attached with below information:\n\n"
                            "%s" % post)

    def _download(self, medium_type, medium_url, target_folder):
        medium_name = medium_url.split("/")[-1].split("?")[0]
        if medium_type == "video":
            if not medium_name.startswith("tumblr"):
                medium_name = "_".join([medium_url.split("/")[-2],
                                        medium_name])

            medium_name += ".mp4"
            medium_url = 'https://vt.tumblr.com/' + medium_name

        file_path = os.path.join(target_folder, medium_name)
        if not os.path.isfile(file_path):
            print("Downloading %s from %s.\n" % (medium_name,
                                                 medium_url))
            retry_times = 0
            while retry_times < RETRY:
                try:
                    resp = requests.get(medium_url,
                                        stream=True,
                                        proxies=self.proxies,
                                        timeout=TIMEOUT)
                    if resp.status_code == 403:
                        retry_times = RETRY
                        print("Access Denied when retrieve %s.\n" % medium_url)
                        raise Exception("Access Denied")
                    with open(file_path, 'wb') as fh:
                        for chunk in resp.iter_content(chunk_size=1024):
                            fh.write(chunk)
                    break
                except:
                    # try again
                    pass
                retry_times += 1
            else:
                try:
                    os.remove(file_path)
                except OSError:
                    pass
                print("Failed to retrieve %s from %s.\n" % (medium_type,
                                                            medium_url))


class CrawlerScheduler(object):

    def __init__(self, tumblr_names, proxies=None):
        self.tumblr_names = tumblr_names
        self.proxies = proxies
        self.queue = Queue.Queue()

    def start(self, original_posts_only=False):
        # create workers
        for x in range(THREADS):
            worker = DownloadWorker(self.queue,
                                    proxies=self.proxies)
            # Setting daemon to True will let the main thread exit
            # even though the workers are blocking
            worker.daemon = True
            worker.start()

        for tumblr_name in self.tumblr_names:
            self.download_media(tumblr_name, original_posts_only)

    def get_post_type(self, post):
        try:
            return post["@type"].lower()
        except KeyError:
            return False
        
    def is_media_post(self, post):
        post_type = self.get_post_type(post)

        if post_type in MEDIA_POST_TYPES:
            return True

    def is_original_post(self, post):
        # print(f"{post = }")
        try:
            print(post["@reblogged-from-name"])
        except KeyError:
            print(f"Original post found! - Queue: {self.queue.qsize()}")
            return True
        
        return False
    
    def download_media(self, tumblr_name, original_posts_only=False):
        if original_posts_only:
            post_filter = self.is_original_post
        else:
            post_filter = lambda x: True

        self.download_photos(tumblr_name, post_filter)
        self.download_videos(tumblr_name, post_filter)

    def download_videos(self, tumblr_name, post_filter=lambda x: True):
        self._download_media(tumblr_name, "video", START_POST_INDEX, post_filter)
        # wait for the queue to finish processing all the tasks from one
        # single tumblr_name
        self.queue.join()
        print("Finish Downloading All the videos from %s" % tumblr_name)

    def download_photos(self, tumblr_name, post_filter=lambda x: True):
        self._download_media(tumblr_name, "photo", START_POST_INDEX, post_filter)
        # wait for the queue to finish processing all the tasks from one
        # single tumblr_name
        self.queue.join()
        print("Finish Downloading All the photos from %s" % tumblr_name)

    def _download_media(self, tumblr_name, medium_type, start, post_filter=lambda x: True):
        target_folder = os.path.join(DOWNLOADS_FOLDER, tumblr_name)

        if not os.path.isdir(target_folder):
            os.makedirs(target_folder)

        base_url = "https://{0}.tumblr.com/api/read?type={1}&num={2}&start={3}"
        start = START_POST_INDEX

        while start < END_POST_INDEX:
            media_url = base_url.format(tumblr_name, medium_type, MEDIA_NUM, start)
            response = requests.get(media_url,
                                    proxies=self.proxies)
            if response.status_code == 404:
                print("Tumblr \"{tumblr_name}\" does not exist")
                break

            try:
                xml_cleaned = re.sub(u'[^\x20-\x7f]+',
                                     u'', response.content.decode('utf-8'))

                response_file = "{0}_{1}_{2}_{3}.response.xml".format(tumblr_name, medium_type, MEDIA_NUM, start)
                with open(os.path.join(DOWNLOADS_FOLDER, response_file), "w") as text_file:
                    text_file.write(xml_cleaned)

                data = xmltodict.parse(xml_cleaned)
                posts = data["tumblr"]["posts"]["post"]
                for post in posts:
                    # by default it is switched to false to generate less files,
                    # as anyway you can extract this from bulk xml files.
                    if EACH_POST_AS_SEPARATE_JSON:
                        post_json_file = "{0}_post_id_{1}.post.json".format(tumblr_name, post['@id'])
                        with open(os.path.join(DOWNLOADS_FOLDER, post_json_file), "w") as text_file:
                            text_file.write(json.dumps(post))

                    medium_type = self.get_post_type(post)
                    if medium_type in MEDIA_POST_TYPES and post_filter(post):
                        try:
                            # if post has photoset, walk into photoset for each photo
                            photoset = post["photoset"]["photo"]
                            for photo in photoset:
                                self.queue.put((medium_type, photo, target_folder))
                        except Exception as e:
                            print(e)
                            # select the largest resolution
                            # usually in the first element
                            self.queue.put((medium_type, post, target_folder))
                start += MEDIA_NUM
            except KeyError:
                break
            except UnicodeDecodeError:
                print("Cannot decode response data from URL %s" % media_url)
                continue
            except Exception as e:
                raise e
                print("Unknown xml-vulnerabilities from URL %s" % media_url)
                continue


def usage():
    print("1. Please create file tumblr_names.txt under this same directory.\n"
          "2. In tumblr_names.txt, you can specify tumblr tumblr_names separated by "
          "comma/space/tab/CR. Accept multiple lines of text\n"
          "3. Save the file and retry.\n\n"
          "Sample File Content:\ntumblr_name1,tumblr_name2\n\n"
          "Or use command line options:\n\n"
          "Sample:\npython tumblr-photo-video-ripper.py tumblr_name1,tumblr_name2\n\n\n")
    print(u"未找到tumblr_names.txt文件，请创建.\n"
          u"请在文件中指定Tumblr站点名，并以 逗号/空格/tab/表格鍵/回车符 分割，支持多行.\n"
          u"保存文件并重试.\n\n"
          u"例子: tumblr_name1,tumblr_name2\n\n"
          u"或者直接使用命令行参数指定站点\n"
          u"例子: python tumblr-photo-video-ripper.py tumblr_name1,tumblr_name2")


def illegal_json():
    print("Illegal JSON format in file 'proxies.json'.\n"
          "Please refer to 'proxies_sample1.json' and 'proxies_sample2.json'.\n"
          "And go to http://jsonlint.com/ for validation.\n\n\n")
    print(u"文件proxies.json格式非法.\n"
          u"请参照示例文件'proxies_sample1.json'和'proxies_sample2.json'.\n"
          u"然后去 http://jsonlint.com/ 进行验证.")


def parse_tumblr_names(filename):
    with open(filename, "r") as f:
        raw_tumblr_names = f.read().rstrip().lstrip()

    raw_tumblr_names = raw_tumblr_names.replace("\t", ",") \
                         .replace("\r", ",") \
                         .replace("\n", ",") \
                         .replace(" ", ",")
    raw_tumblr_names = raw_tumblr_names.split(",")

    tumblr_names = list()
    for raw_tumblr_name in raw_tumblr_names:
        tumblr_name = raw_tumblr_name.lstrip().rstrip()
        if tumblr_name:
            tumblr_names.append(tumblr_name)
    return tumblr_names


if __name__ == "__main__":
    cur_dir = os.path.dirname(os.path.realpath(__file__))
    tumblr_names = None

    proxies = None
    proxy_path = os.path.join(cur_dir, "proxies.json")
    if os.path.exists(proxy_path):
        with open(proxy_path, "r") as fj:
            try:
                proxies = json.load(fj)
                if proxies is not None and len(proxies) > 0:
                    print("You are using proxies.\n%s" % proxies)
            except:
                illegal_json()
                sys.exit(1)

    if len(sys.argv) < 2:
        # check the tumblr_names file
        filename = os.path.join(cur_dir, "tumblr_names.txt")
        if os.path.exists(filename):
            tumblr_names = parse_tumblr_names(filename)
        else:
            usage()
            sys.exit(1)
    else:
        tumblr_names = sys.argv[1].split(",")

    if len(tumblr_names) == 0 or tumblr_names[0] == "":
        usage()
        sys.exit(1)

    crawler = CrawlerScheduler(tumblr_names, proxies=proxies)
    crawler.start(original_posts_only=True)     # IMPR!

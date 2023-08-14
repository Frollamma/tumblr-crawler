from bs4 import BeautifulSoup
import requests

with open("regular-body_example.html", "r") as f:
    content = f.read()

soup = BeautifulSoup(content, "html.parser")

imgs = soup.find_all("img")
print(imgs)
for img in imgs:
    srcset = img["srcset"]
    srcset = srcset.strip().strip(",").strip().split("\n")
    best_quality_image_src = srcset[-1].lstrip().rstrip().split(" ")[0]
    print(best_quality_image_src)
    res = requests.get(best_quality_image_src)
    # print(res.text)
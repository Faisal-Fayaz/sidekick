import requests

response = requests.get("https://example.com", verify=False)
session = requests.Session()
session.verify = False

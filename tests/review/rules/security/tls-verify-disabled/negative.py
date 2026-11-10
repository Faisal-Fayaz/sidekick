import requests

response = requests.get("https://example.com", verify=True)
session = requests.Session()
session.verify = True

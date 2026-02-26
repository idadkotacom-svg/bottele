import os, requests
from dotenv import load_dotenv
load_dotenv()
token = os.getenv('FB_PAGE_ACCESS_TOKEN')
res = requests.get(f'https://graph.facebook.com/v19.0/me/accounts?access_token={token}').json()
print("Attached Pages:", res)

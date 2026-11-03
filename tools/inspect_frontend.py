import json
from playwright.sync_api import sync_playwright
url='https://www.obvibase.com/app/?location='+__import__('urllib.parse',fromlist=['quote']).quote(json.dumps({'type':'table','databaseId':'INEwAfkgyn76','queryPath':{'recordPath':[],'columnPath':['1']}}))
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium-browser',headless=True,args=['--no-sandbox'])
 page=b.new_page()
 page.on('request',lambda r: print(r.method,r.url) if 'database_data' in r.url else None)
 page.on('response',lambda r: print('response',r.status,r.url) if 'database_data' in r.url else None)
 page.goto(url,wait_until='domcontentloaded',timeout=60000)
 page.wait_for_timeout(15000)
 print(page.locator('body').inner_text()[:4000])
 b.close()

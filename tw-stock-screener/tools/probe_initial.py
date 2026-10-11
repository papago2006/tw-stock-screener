"""Manual official endpoint availability probe; compressed responses are evidence only."""
import urllib.request,json,datetime,pathlib,gzip,sys
sys.stdout.reconfigure(encoding='utf-8')
urls={
 'twse_day':'https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date=20250930&type=ALLBUT0999',
 'twse_month':'https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date=20250901&stockNo=2330',
 'tpex_day':'https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date=2025/09/30&id=&response=json',
 'tpex_month':'https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?date=2025/09/01&code=6488&response=json',
 'isin_twse':'https://isin.twse.com.tw/isin/C_public.jsp?strMode=2',
 'isin_tpex':'https://isin.twse.com.tw/isin/C_public.jsp?strMode=4',
 'tpex_spec':'https://www.tpex.org.tw/openapi/swagger.json'}
root=pathlib.Path(__file__).resolve().parents[1]/'docs'/'probes';root.mkdir(exist_ok=True)
for name,url in urls.items():
 try:
  req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
  with urllib.request.urlopen(req,timeout=30) as r:
   b=r.read(); (root/(name+'.raw.gz')).write_bytes(gzip.compress(b))
   print(name,r.status,len(b))
   try:
    j=json.loads(b)
    if isinstance(j,dict):
     print('keys',list(j)[:20]);print(str(j)[:1600])
   except Exception: print(b[:80])
 except Exception as e: print(name,type(e).__name__,str(e))

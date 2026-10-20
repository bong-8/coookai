import asyncio, base64, subprocess, time, sys, os
from playwright.async_api import async_playwright
srv=subprocess.Popen([sys.executable,'-m','http.server','8766'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); time.sleep(1)
ITEMS=[('onion',0.35),('tomato',0.45),('plate',0.95),('pot',0.95),('plate-dinner',0.95),('bowl-soup',0.95),('pot-stew',0.95)]
os.makedirs('items',exist_ok=True)
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(args=['--use-gl=swiftshader','--enable-unsafe-swiftshader','--ignore-gpu-blocklist'])
        pg=await b.new_page(); await pg.goto('http://localhost:8766/render_items.html'); await pg.wait_for_function('window.ready===true',timeout=20000)
        for n,t in ITEMS:
            u=await pg.evaluate(f"renderItem('FOOD_src/Models/GLB format/{n}.glb',{t})")
            open(f'items/{n}.png','wb').write(base64.b64decode(u.split(',')[1]))
        await b.close()
try: asyncio.run(main())
finally: srv.terminate()

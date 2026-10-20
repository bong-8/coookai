import asyncio, base64, subprocess, time, sys, os
from playwright.async_api import async_playwright
srv=subprocess.Popen([sys.executable,'-m','http.server','8765'],cwd='.',stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); time.sleep(1)
CH={'p1':('character-male-b',0x2f6fe0),'p2':('character-female-e',0x2fae5a)}
DIRS={'SOUTH':0,'EAST':90,'NORTH':180,'WEST':270}
os.makedirs('out',exist_ok=True)
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(args=['--use-gl=swiftshader','--enable-unsafe-swiftshader','--ignore-gpu-blocklist'])
        pg=await b.new_page(); pg.on('console',lambda m:print('console',m.text)); pg.on('pageerror',lambda e:print('err',e))
        await pg.goto('http://localhost:8765/render_chefs.html'); await pg.wait_for_function('window.ready===true',timeout=20000)
        for k,(name,col) in CH.items():
            for dn,deg in DIRS.items():
                u=await pg.evaluate(f"renderOne('{name}',{deg},{col})")
                open(f'out/{k}_{dn}.png','wb').write(base64.b64decode(u.split(',')[1]))
        await b.close()
try: asyncio.run(main())
finally: srv.terminate()

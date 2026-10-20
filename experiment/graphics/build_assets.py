"""Kenney 스킨 에셋 조립 (렌더된 아이템/요리사 PNG → 게임용 PNG).
입력: items/*.png(render_items.py), out/p{1,2}_{DIR}.png(render.py)   출력: OUT_DIR"""
import colorsys, os, sys
from PIL import Image, ImageDraw, ImageFilter
OUT=sys.argv[1] if len(sys.argv)>1 else 'kenney_out'; os.makedirs(OUT,exist_ok=True)
ONION=(244,214,120); TOM=(220,60,50)
def load(n): 
    im=Image.open(f'items/{n}.png').convert('RGBA'); return im.crop(im.getbbox())
def fit(im,box):
    w,h=im.size; s=min(box/w,box/h); return im.resize((max(1,int(w*s)),max(1,int(h*s))),Image.LANCZOS)
def tint_onion(im):
    px=im.load()
    for y in range(im.size[1]):
        for x in range(im.size[0]):
            r,g,b,a=px[x,y]
            if a==0: continue
            h,s,v=colorsys.rgb_to_hsv(r/255,g/255,b/255)
            h=0.125; s=min(s*0.55,0.55); v=min(v*1.18+0.12,1.0)   # 연노랑
            r2,g2,b2=colorsys.hsv_to_rgb(h,s,v); px[x,y]=(int(r2*255),int(g2*255),int(b2*255),a)
    return im
onion=tint_onion(load('onion')); tomato=load('tomato'); plate=load('plate'); pot=load('pot'); dinner=load('plate-dinner')
def center(canvas,im,cx,cy): canvas.alpha_composite(im,(int(cx-im.size[0]/2),int(cy-im.size[1]/2)))
def save(im,name): im.save(f'{OUT}/{name}.png')
S=160
def floor(c):
    im=Image.new('RGBA',(S,S),c); d=ImageDraw.Draw(im); d.line([0,0,S,0],fill=(210,188,150),width=2); d.line([0,0,0,S],fill=(210,188,150),width=2); return im
save(floor((233,214,178,255)),'floor_a'); save(floor((224,203,165,255)),'floor_b')
def counter():
    im=floor((228,208,170,255))
    sh=Image.new('RGBA',(S,S),(0,0,0,0)); ImageDraw.Draw(sh).rounded_rectangle([9,14,S-3,S-3],16,fill=(0,0,0,80)); im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(4)))
    d=ImageDraw.Draw(im); d.rounded_rectangle([6,6,S-7,S-7],14,fill=(150,104,66)); d.rounded_rectangle([6,6,S-7,S-29],14,fill=(196,150,100)); d.line([18,18,S-19,18],fill=(214,172,124),width=3); return im
CT=counter(); save(CT,'counter')
def station(items):
    im=CT.copy()
    for it,cx,cy,sz in items: center(im,fit(it,sz),cx,cy)
    return im
save(station([(onion,50,60,64),(onion,108,56,64),(onion,80,92,64)]),'tile_onions')
save(station([(tomato,50,60,64),(tomato,108,56,64),(tomato,80,92,64)]),'tile_tomatoes')
save(station([(plate,80,78,100),(plate,80,66,100)]),'tile_dishes')
im=CT.copy(); ImageDraw.Draw(im).ellipse([30,26,130,106],fill=(86,170,110)); center(im,fit(dinner,92),80,64); save(im,'tile_serve')
im=CT.copy(); center(im,fit(pot,104),80,92); save(im,'tile_pot')
# 아이템(들고 있는 것/바닥)
save(fit(onion,96),'item_onion'); save(fit(tomato,96),'item_tomato')
def soup_plate(items):
    base=fit(plate,96); im=Image.new('RGBA',(96,96),(0,0,0,0)); center(im,base,48,48)
    o,t=items.count('o'),items.count('t'); n=o+t
    c=tuple(int((ONION[k]*o+TOM[k]*t)/n) for k in range(3))
    ov=Image.new('RGBA',(96,96),(0,0,0,0)); ImageDraw.Draw(ov).ellipse([24,26,72,66],fill=c+(255,)); im.alpha_composite(ov); return im
save(fit(plate,96),'item_dish'); save(soup_plate('ooo'),'item_soup_o'); save(soup_plate('ttt'),'item_soup_t'); save(soup_plate('oot'),'item_soup_m')
# 칩
def chip(kind):
    s=64; im=Image.new('RGBA',(s,s),(0,0,0,0)); d=ImageDraw.Draw(im)
    if kind=='empty':
        for a in range(0,360,40): d.arc([6,6,s-6,s-6],a,a+22,fill=(120,100,80,255),width=4)
        return im
    col=ONION if kind=='o' else TOM
    d.ellipse([2,2,s-2,s-2],fill=(255,255,255,255),outline=col+(255,),width=7)
    center(im,fit(onion if kind=='o' else tomato,38),s/2,s/2+1); return im
for k in 'ot': save(chip(k),'chip_'+k)
save(chip('empty'),'chip_empty')
# 요리사
for p in ('p1','p2'):
    for d in ('SOUTH','EAST','NORTH','WEST'):
        im=Image.open(f'out/{p}_{d}.png').convert('RGBA'); save(im.resize((192,192),Image.LANCZOS),f'chef_{p}_{d}')
print(sorted(os.listdir(OUT)))

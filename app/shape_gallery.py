"""Deterministic schematic solids; SVG and PNG use the same drawing operations."""
import math
import re


SHAPES={'三角形':'triangle','长方形':'rectangle','圆':'circle','正方体':'cube',
        '长方体':'cuboid','球体':'sphere','球':'sphere','圆锥':'cone','圆柱':'cylinder',
        '五棱锥':'pentagonal_pyramid','圆台':'frustum'}


def enumerated_gallery(stem):
    # Only an explicit numbered list in the question, never its answer options,
    # is enough evidence to synthesize an illustration without inventing facts.
    text=re.split(r'(?:^|\s)[A-H][.．、]',stem,maxsplit=1)[0]
    names=re.findall(r'[①②③④⑤⑥]\s*('+ '|'.join(sorted(SHAPES,key=len,reverse=True))+r')(?=[\s；;，,。、]|$)',text)
    if len(names)<2:return None
    return dict(kind='shape_gallery',shapes=[dict(shape=SHAPES[name],label='①②③④⑤⑥'[i]) for i,name in enumerate(names[:6])],
                caption='按题干顺序绘制的图形示意，编号与题目对应',alt='；'.join(f'{i+1}：{n}' for i,n in enumerate(names[:6])))


def draw_gallery(c,diagram):
    count=len(diagram.shapes);columns=3 if count>4 else 2 if count>1 else 1
    rows=math.ceil(count/columns);cell_w=760/columns;cell_h=360/rows
    for i,item in enumerate(diagram.shapes):
        x=20+(i%columns+.5)*cell_w;y=12+(i//columns+.45)*cell_h
        scale=min(cell_w/190,cell_h/168,1.25)
        def line(points,hidden=False):c.line([(x+a*scale,y+b*scale) for a,b in points],dashed=hidden)
        def arc(cy,rx,ry,start=0,end=2*math.pi,hidden=False):
            # Enough samples for smooth curves in both export formats; larger
            # steps on hidden arcs preserve a visible dash pattern in the PNG.
            steps=16 if hidden else 60
            line([(rx*math.cos(start+(end-start)*j/steps),cy+ry*math.sin(start+(end-start)*j/steps)) for j in range(steps+1)],hidden)
        shape=item.shape
        if shape=='triangle':line([(-55,42),(0,-50),(55,42),(-55,42)])
        elif shape=='rectangle':line([(-65,-38),(65,-38),(65,42),(-65,42),(-65,-38)])
        elif shape=='circle':c.circle(x,y,50*scale)
        elif shape in {'cube','cuboid'}:
            w,h=(62,62) if shape=='cube' else (90,52)
            a=(-w/2-16,-h/2+13);b=(a[0]+w,a[1]);cc=(b[0],b[1]+h);d=(a[0],a[1]+h)
            back=[(p[0]+32,p[1]-26) for p in (a,b,cc,d)]
            line([a,b,cc,d,a]);line([a,back[0],back[1],b]);line([back[1],back[2],cc])
            line([back[0],back[3],back[2]],True);line([d,back[3]],True)
        elif shape=='sphere':
            c.circle(x,y,52*scale);arc(0,52,16,0,math.pi);arc(0,52,16,math.pi,2*math.pi,True)
        elif shape in {'cone','cylinder','frustum'}:
            arc(42,52,17,0,math.pi);arc(42,52,17,math.pi,2*math.pi,True)
            if shape=='cone':line([(-52,42),(0,-57),(52,42)])
            else:
                top=52 if shape=='cylinder' else 29
                arc(-46,top,17 if shape=='cylinder' else 10)
                line([(-52,42),(-top,-46)]);line([(52,42),(top,-46)])
        elif shape=='pentagonal_pyramid':
            base=[(58*math.cos(-math.pi/2+j*2*math.pi/5),34+22*math.sin(-math.pi/2+j*2*math.pi/5)) for j in range(5)]
            apex=(0,-58)
            for j,p in enumerate(base):
                q=base[(j+1)%5];line([p,q],p[1]<34 and q[1]<34)
                line([apex,p],j==0)
        c.text(x,12+(i//columns+1)*cell_h-5,item.label or '①②③④⑤⑥'[i],22)

"""Typed teaching figures. Coordinates, rays and curves are drawn by the server,
never by executing model code or accepting model-supplied HTML/SVG.
The same drawing operations produce accessible SVG and a PNG for PPTX/review.
"""
import base64
import html
import io
import math
import re
from fractions import Fraction
from pathlib import Path
from typing import Annotated, Literal

from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ShapeModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, allow_inf_nan=False)

    @model_validator(mode='before')
    @classmethod
    def readable_labels(cls, values):
        if not isinstance(values,dict):
            return values
        def convert_label(value):
            if not isinstance(value,str):
                return value
            for command,symbol in [('\\geq','≥'),('\\leq','≤'),('\\ge','≥'),('\\le','≤'),('\\neq','≠'),('\\ne','≠'),
                                   ('\\times','×'),('\\div','÷'),('\\Omega','Ω'),('\\pm','±'),('\\to','→'),('^2','²'),('^3','³')]:
                value=value.replace(command,symbol)
            value=re.sub(r'\\(?:mathrm|text)\{([^{}]*)\}',r'\1',value).replace('$','')
            if '\\' in value or '{' in value or '}' in value:
                raise ValueError('图内标签请使用纯文本和 Unicode 数学符号，不支持复杂 LaTeX')
            return value
        values=dict(values)
        for key in ('label','x_label','y_label','source_label','current_label'):
            if key in values:values[key]=convert_label(values[key])
        for key in ('resistors','nodes'):
            if isinstance(values.get(key),list):values[key]=[convert_label(v) for v in values[key]]
        return values


class Figure(ShapeModel):
    caption: str = Field(min_length=1, max_length=180, description='解释图与本题的关系；示例图须明确标明示例，不能冒充原题图')
    alt: str = Field(min_length=1, max_length=300, description='包含关键数值与关系的图像文字说明')


class Interval(ShapeModel):
    start: float | None = None
    end: float | None = None
    start_closed: bool = False
    end_closed: bool = False
    label: str = Field(min_length=1, max_length=35)


class NumberLine(Figure):
    kind: Literal['number_line']
    minimum: float
    maximum: float
    ticks: list[float] = Field(min_length=2, max_length=11)
    intervals: list[Interval] = Field(min_length=1, max_length=3, description='每个范围单独一行，可比较含等号与不含等号；null 端点代表无穷')

    @model_validator(mode='after')
    def bounds(self):
        if not 1e-6 < self.maximum-self.minimum < 1e9:
            raise ValueError('数轴范围无效')
        if any(not self.minimum <= x <= self.maximum for x in self.ticks) or len(set(self.ticks)) != len(self.ticks):
            raise ValueError('数轴刻度必须唯一且在范围内')
        endpoints={x for interval in self.intervals for x in (interval.start,interval.end) if x is not None}
        # The viewport may grow; the mathematical interval must remain unchanged.
        padding=(self.maximum-self.minimum)*.1
        if endpoints:
            self.minimum=min(self.minimum,min(endpoints)-padding)
            self.maximum=max(self.maximum,max(endpoints)+padding)
            if self.maximum-self.minimum>=1e9:raise ValueError('数轴范围无效')
        for interval in self.intervals:
            a, b = interval.start, interval.end
            if a is not None and not self.minimum < a < self.maximum or b is not None and not self.minimum < b < self.maximum:
                raise ValueError('有限端点必须位于数轴范围内部')
            if a is not None and b is not None and (a>b or a==b and not (interval.start_closed and interval.end_closed)):
                raise ValueError(f'区间“{interval.label}”的 start={a} 必须小于 end={b}；右射线 end=null，左射线 start=null。表示单点时 start=end 且两个 closed 均为 true')
            if a is None and interval.start_closed or b is None and interval.end_closed:
                raise ValueError('无穷端点不能闭合')
            # Endpoints determine the mathematics; tick labels are presentation.
            # Add missing labels without moving endpoints or changing inclusivity.
        self.ticks=sorted(endpoints|set(x for x in self.ticks if x not in endpoints))
        if len(self.ticks)>11:
            mandatory=endpoints|{self.ticks[0],self.ticks[-1]}
            optional=[x for x in self.ticks if x not in mandatory]
            self.ticks=sorted(mandatory|set(optional[:11-len(mandatory)]))
        return self


class PlotPoint(ShapeModel):
    x: float
    y: float
    label: str = Field(default='', max_length=25)


class Curve(ShapeModel):
    coefficients: list[float] | None = Field(default=None,min_length=3,max_length=3,
        description='仅二次函数用 [二次项系数,一次项系数,常数项]；直线优先填 slope/intercept。旧格式 y=x+4 应为 [0,1,4]，不是 [1,4,0]')
    slope: float | None = Field(default=None,description='直线 y=kx+b 的斜率 k；直线优先使用此字段')
    intercept: float | None = Field(default=None,description='直线 y=kx+b 的截距 b；如 y=x+4 填 4')
    label: str = Field(min_length=1, max_length=35)

    @model_validator(mode='after')
    def polynomial(self):
        if self.slope is not None and self.intercept is not None:
            self.coefficients=[0,self.slope,self.intercept]
        elif self.coefficients is None:
            raise ValueError('曲线需提供 slope/intercept 或三个 coefficients 系数')
        else:
            # Compatibility for an unambiguous linear label with legacy [k,b,0].
            # Do not infer coefficients from points or reinterpret quadratic labels.
            label=self.label.replace('−','-').replace('½','1/2').replace(' ','')
            number=r'\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?'
            match=re.search(r'(?:^|[:：])y=([+-]?(?:'+number+r')?)x([+-]'+number+r')?$',label)
            if match:
                try:
                    slope=float(Fraction(match[1])) if match[1] not in ('','+','-') else (-1 if match[1]=='-' else 1)
                    intercept=float(Fraction(match[2])) if match[2] else 0
                except (ValueError,ZeroDivisionError):
                    return self
                if self.coefficients==[slope,intercept,0] and self.coefficients!=[0,slope,intercept]:
                    self.coefficients=[0,slope,intercept]
                    self.slope,self.intercept=slope,intercept
        return self


class Cartesian(Figure):
    kind: Literal['cartesian']
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    x_label: str = Field(default='x', max_length=18)
    y_label: str = Field(default='y', max_length=18)
    curves: list[Curve] = Field(min_length=1, max_length=2)
    points: list[PlotPoint] = Field(default_factory=list, max_length=5)

    @model_validator(mode='after')
    def bounds(self):
        if not 1e-6 < self.x_max-self.x_min < 1e9 or not 1e-6 < self.y_max-self.y_min < 1e9:
            raise ValueError('坐标范围无效')
        # An annotation can be a free point or a projection, not a point on a curve.
        # Expand only the viewport; preserve the requested point coordinates.
        self.x_min=min(self.x_min,0,*(p.x for p in self.points))
        self.x_max=max(self.x_max,0,*(p.x for p in self.points))
        self.y_min=min(self.y_min,0,*(p.y for p in self.points))
        self.y_max=max(self.y_max,0,*(p.y for p in self.points))
        if self.x_max-self.x_min>=1e9 or self.y_max-self.y_min>=1e9:
            raise ValueError('坐标范围过大，无法绘制')
        return self


class Vertex(ShapeModel):
    id: str = Field(min_length=1, max_length=8)
    x: float = Field(ge=-1e6, le=1e6, description='几何关系坐标，可负；绘制时等比例缩放')
    y: float = Field(ge=-1e6, le=1e6)
    label: str = Field(default='', max_length=18)


class Segment(ShapeModel):
    start: str
    end: str
    dashed: bool = False
    label: str = Field(default='', max_length=18)


class Circle(ShapeModel):
    center: str
    radius: float = Field(gt=0, le=1e6)


class Geometry(Figure):
    kind: Literal['geometry']
    vertices: list[Vertex] = Field(min_length=1, max_length=10)
    segments: list[Segment] = Field(default_factory=list, max_length=16)
    circles: list[Circle] = Field(default_factory=list, max_length=3)

    @model_validator(mode='after')
    def references(self):
        lookup = {v.id:v for v in self.vertices}
        if len(lookup) != len(self.vertices) or not (self.segments or self.circles):
            raise ValueError('几何图需有图形及唯一顶点')
        for segment in self.segments:
            if segment.start not in lookup or segment.end not in lookup or segment.start == segment.end:
                raise ValueError('几何线段端点引用无效')
        for circle in self.circles:
            if circle.center not in lookup:
                raise ValueError('圆心引用无效')
        return self


class Circuit(Figure):
    kind: Literal['circuit']
    topology: Literal['series', 'parallel']
    resistors: list[str] = Field(min_length=1, max_length=3, description='每个电阻的标注，例如 R₁ = 10 Ω；没有题设数值时不得编造')
    source_label: str = Field(min_length=1, max_length=30)
    current_label: str = Field(default='I', max_length=25)

    @model_validator(mode='after')
    def labels(self):
        if any(not label.strip() or len(label)>25 for label in self.resistors):
            raise ValueError('电阻标注需为 1–25 字')
        return self


class Process(Figure):
    kind: Literal['process']
    nodes: list[Annotated[str,Field(min_length=1,max_length=48)]] = Field(min_length=2, max_length=4, description='有逻辑关系的短语，建议每项 24 字以内，含公式最多 48 字；不能仅列知识点标题')

    @model_validator(mode='after')
    def short_labels(self):
        if any(not n.strip() for n in self.nodes):
            raise ValueError('关系图节点不能为空')
        return self


class GalleryShape(ShapeModel):
    shape: Literal['triangle','rectangle','circle','cube','cuboid','sphere','cone','cylinder','pentagonal_pyramid','frustum']
    label: str = Field(default='', max_length=25)


class ShapeGallery(Figure):
    kind: Literal['shape_gallery']
    shapes: list[GalleryShape] = Field(min_length=1,max_length=6,description='按题干的对象顺序绘制平面或立体图形；不把选项中未出现的图形加入题图')


Diagram = Annotated[NumberLine | Cartesian | Geometry | Circuit | Process | ShapeGallery, Field(discriminator='kind')]

GREEN, GOLD, INK, MUTED, BG = '#287253', '#aa7031', '#254b3c', '#647d6c', '#f4f8ed'


def font_path():
    for path in ('C:/Windows/Fonts/msyh.ttc', '/mnt/c/Windows/Fonts/msyh.ttc',
                 '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
                 '/System/Library/Fonts/PingFang.ttc'):
        if Path(path).is_file():
            return path
    raise RuntimeError('缺少中文绘图字体；Linux 请安装 fonts-noto-cjk')


class Canvas:
    def __init__(self, alt):
        self.parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 400" role="img"><title>{html.escape(alt)}</title>',
                      f'<rect width="800" height="400" rx="20" fill="{BG}"/>']
        self.image = Image.new('RGB', (1600, 800), BG)
        self.draw = ImageDraw.Draw(self.image)
        self.fonts = {}

    def line(self, points, color=GREEN, width=3, dashed=False):
        points = [(round(x, 3), round(y, 3)) for x,y in points]
        dash = ' stroke-dasharray="8 6"' if dashed else ''
        self.parts.append(f'<polyline points="'+ ' '.join(f'{x},{y}' for x,y in points)+f'" fill="none" stroke="{color}" stroke-width="{width}" stroke-linejoin="round"{dash}/>')
        if dashed:
            for (x,y),(xx,yy) in zip(points,points[1:]):
                length=math.hypot(xx-x,yy-y)
                for d in range(0, math.ceil(length), 14):
                    a,b=d/max(length,1e-9),min(d+8,length)/max(length,1e-9)
                    self.draw.line([(2*(x+(xx-x)*a),2*(y+(yy-y)*a)),(2*(x+(xx-x)*b),2*(y+(yy-y)*b))],fill=color,width=2*width)
        else:
            self.draw.line([(2*x,2*y) for x,y in points], fill=color, width=2*width, joint='curve')

    def arrow(self, x, y, xx, yy, color=GREEN, width=3):
        self.line([(x,y),(xx,yy)], color, width)
        angle=math.atan2(yy-y,xx-x)
        self.line([(xx-12*math.cos(angle-.5),yy-12*math.sin(angle-.5)),(xx,yy),
                   (xx-12*math.cos(angle+.5),yy-12*math.sin(angle+.5))],color,width)

    def circle(self, x, y, r=7, fill=BG, stroke=GREEN, width=3):
        self.parts.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{fill}" stroke="{stroke}" stroke-width="{width}"/>')
        self.draw.ellipse((2*(x-r),2*(y-r),2*(x+r),2*(y+r)),fill=None if fill=='none' else fill,outline=stroke,width=2*width)

    def rect(self, x, y, w, h, fill=BG, stroke=GREEN):
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
        self.draw.rectangle((x*2,y*2,(x+w)*2,(y+h)*2),fill=fill,outline=stroke,width=4)

    def text(self, x, y, value, size=20, color=INK, anchor='middle'):
        # Plain labels only; no SVG markup, external URLs or executable expressions.
        value=str(value)
        self.parts.append(f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-family="Microsoft YaHei,PingFang SC,sans-serif" font-size="{size}" fill="{color}">{html.escape(value)}</text>')
        if size not in self.fonts:
            self.fonts[size]=ImageFont.truetype(font_path(),size*2)
        font=self.fonts[size]; w=self.draw.textlength(value,font=font)
        offset=0 if anchor=='start' else w if anchor=='end' else w/2
        self.draw.text((x*2-offset,y*2),value,font=font,fill=color,anchor='ls')


def render_diagram(spec):
    from pydantic import TypeAdapter
    d=TypeAdapter(Diagram).validate_python(spec)
    c=Canvas(d.alt)
    if isinstance(d, NumberLine):
        scale=lambda x:70+(x-d.minimum)/(d.maximum-d.minimum)*650
        n=len(d.intervals)
        for i,r in enumerate(d.intervals):
            y=110+i*108 if n==3 else 140+i*145 if n==2 else 220
            color=GREEN if i==0 else GOLD
            c.text(70,y-60,r.label,22,color,'start')
            c.arrow(55,y,746,y,MUTED,2); c.text(754,y+6,'x',18)
            for tick in sorted(d.ticks):
                x=scale(tick); c.line([(x,y-5),(x,y+5)],MUTED,2); c.text(x,y+30,f'{tick:g}',18)
            start=scale(r.start) if r.start is not None else 55
            end=scale(r.end) if r.end is not None else 746
            c.line([(start,y-17),(end,y-17)],color,5)
            if r.start is None: c.arrow(start+20,y-17,start,y-17,color)
            if r.end is None: c.arrow(end-20,y-17,end,y-17,color)
            for value,closed in ((r.start,r.start_closed),(r.end,r.end_closed)):
                if value is not None:
                    x=scale(value); c.line([(x,y-17),(x,y)],color,2)
                    c.circle(x,y,7,color if closed else BG,color)
    elif isinstance(d, Cartesian):
        sx=lambda x:85+(x-d.x_min)/(d.x_max-d.x_min)*620
        sy=lambda y:330-(y-d.y_min)/(d.y_max-d.y_min)*270
        for i in range(6):
            x=d.x_min+(d.x_max-d.x_min)*i/5; y=d.y_min+(d.y_max-d.y_min)*i/5
            c.line([(sx(x),60),(sx(x),330)],'#dfe7da',1)
            c.line([(85,sy(y)),(705,sy(y))],'#dfe7da',1)
            c.text(sx(x),355,f'{x:.3g}',14); c.text(72,sy(y)+5,f'{y:.3g}',14,anchor='end')
        c.arrow(85,sy(0),725,sy(0),MUTED,2); c.text(750,sy(0)+5,d.x_label,16)
        c.arrow(sx(0),330,sx(0),40,MUTED,2); c.text(sx(0)+8,27,d.y_label,16,anchor='start')
        for i,curve in enumerate(d.curves):
            a,b,cc=curve.coefficients; color=GREEN if i==0 else GOLD; path=[]
            c.text(145+i*300,387,curve.label,17,color,'start')
            for k in range(601):
                x=d.x_min+(d.x_max-d.x_min)*k/600; y=a*x*x+b*x+cc
                if d.y_min<=y<=d.y_max: path.append((sx(x),sy(y)))
                elif path:
                    if len(path)>1:c.line(path,color)
                    path=[]
            if len(path)>1:c.line(path,color)
        for point in d.points:
            x,y=sx(point.x),sy(point.y);c.circle(x,y,5,GOLD,GOLD)
            c.text(x+10 if x<560 else x-10,y-12,point.label or f'({point.x:g}, {point.y:g})',16,GOLD,'start' if x<560 else 'end')
    elif isinstance(d, Geometry):
        lookup={v.id:v for v in d.vertices}
        xs=[v.x for v in d.vertices];ys=[v.y for v in d.vertices]
        for circle in d.circles:
            v=lookup[circle.center]
            xs.extend([v.x-circle.radius,v.x+circle.radius]);ys.extend([v.y-circle.radius,v.y+circle.radius])
        left,right,bottom,top=min(xs),max(xs),min(ys),max(ys)
        scale=min(620/max(right-left,1e-6),290/max(top-bottom,1e-6))
        points={v.id:(400+(v.x-(left+right)/2)*scale,195-(v.y-(top+bottom)/2)*scale) for v in d.vertices}
        for circle in d.circles:
            x,y=points[circle.center];c.circle(x,y,circle.radius*scale,fill='none')
        for s in d.segments:
            a,b=points[s.start],points[s.end];c.line([a,b],dashed=s.dashed)
            if s.label:c.text((a[0]+b[0])/2+12,(a[1]+b[1])/2-12,s.label,18,GOLD)
        for v in d.vertices:
            x,y=points[v.id];c.circle(x,y,3,GREEN);c.text(x+13,y-12,v.label or v.id,20,anchor='start')
        c.text(35,380,'关系示意 · 长度与角度以题设为准',16,MUTED,'start')
    elif isinstance(d, Circuit):
        # A closed circuit: battery at bottom, conventional current along the top.
        c.line([(170,290),(365,290)]);c.line([(395,290),(630,290)])
        c.line([(365,260),(365,320)],INK,4);c.line([(395,274),(395,306)],INK,4)
        c.text(350,250,'+',18);c.text(410,250,'−',18);c.text(380,355,d.source_label,22)
        if d.topology=='series':
            c.line([(170,290),(170,120),(630,120),(630,290)])
            for i,label in enumerate(d.resistors):
                x=220+i*360/max(1,len(d.resistors)-1) if len(d.resistors)>1 else 400
                c.rect(x-38,106,76,28);c.text(x,91,label,18)
        else:
            for i,label in enumerate(d.resistors):
                y=70+i*65
                c.line([(170,290),(170,y),(630,y),(630,290)])
                c.rect(360,y-13,80,26);c.text(400,y-22,label,18)
            c.circle(170,200,4,GREEN);c.circle(630,200,4,GREEN)
        c.arrow(125,240,125,160,GOLD);c.text(100,205,d.current_label,18,GOLD,'end')
    elif isinstance(d, ShapeGallery):
        from .shape_gallery import draw_gallery
        draw_gallery(c,d)
    else:
        # Short, connected causal statements rather than decorative topic tags.
        count=len(d.nodes); width=150; gap=40; start=(800-count*width-(count-1)*gap)/2
        for i,node in enumerate(d.nodes):
            x=start+i*(width+gap);c.rect(x,104,width,192)
            lines=[node[j:j+7] for j in range(0,len(node),7)]
            for j,line in enumerate(lines):c.text(x+width/2,200+(j-(len(lines)-1)/2)*25,line,18)
            if i<count-1:c.arrow(x+width+7,200,x+width+gap-7,200)
    raw=io.BytesIO();c.image.save(raw,format='PNG')
    svg=''.join(c.parts)+'</svg>'
    return dict(kind=d.kind,caption=d.caption,alt=d.alt,
                svg='data:image/svg+xml;base64,'+base64.b64encode(svg.encode()).decode(),
                png='data:image/png;base64,'+base64.b64encode(raw.getvalue()).decode())

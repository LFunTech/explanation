"""Native micro-frontend assets assembled from the same trusted player sources."""
from .config import ROOT
from .exporters import inline_math_assets


def runtime_module():
    _,katex,auto=inline_math_assets()
    shared=['question.js','answer-dialog.js']
    def factory(name,files):
        scripts='\n'.join((ROOT/'web'/file).read_text(encoding='utf-8') for file in files)
        return f'''export function {name}(scope) {{
const {{window,document,ResizeObserver,requestAnimationFrame,cancelAnimationFrame,setTimeout,clearTimeout,Audio}}=scope;
const self=window,globalThis=window,speechSynthesis=window.speechSynthesis;
{katex}
{auto}
const renderMathInElement=window.renderMathInElement;
{scripts}
return window.CoursewareContent;
}}
'''
    return factory('createContent',shared+['layout-metrics.js','question-pagination.js','slide-pagination.js','content.js'])+factory('createShell',shared+['player.js'])


def styles(kind):
    math,_,_=inline_math_assets()
    names=(['player.css','presentation.css','question.css','viewport-player.css','figure-viewer.css'] if kind=='shell'
           else ['question.css','content.css','figure-viewer.css'])
    css='\n'.join((ROOT/'web'/name).read_text(encoding='utf-8') for name in names)
    # These styles live inside a ShadowRoot; document selectors refer to its body.
    import re
    css=css.replace(':root',':host')
    css=re.sub(r'(?<![\w-])(html|body)(?![\w-])','.micro-body',css)
    css=css.replace('height:100dvh','height:100%')
    return math+'\n'+css+'''
:host{display:block;width:100%;height:100%;min-width:0;min-height:0;color:#203e34;font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;line-height:1.6}
.micro-body{width:100%;height:100%;margin:0;overflow:hidden}
#answer-dialog,#figure-dialog{height:100dvh}
'''


def shell_markup():
    html=(ROOT/'web/player.html').read_text(encoding='utf-8')
    body=html.split('<body>',1)[1].split('<script>',1)[0]
    import re
    return re.sub(r'<iframe[^>]+id="lesson-content"[^>]*></iframe>',
                  '<div id="lesson-content" aria-label="课件内容"></div>',body)

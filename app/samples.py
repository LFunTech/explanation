"""明示的本地教学样例，只允许原题及教学范围完全匹配时使用。"""
from copy import deepcopy

from .models import Lesson, Question


def point(title, display, notes):
    return dict(title=title, display=display, notes=notes)


SAMPLES = [
    dict(question=Question(question_id='sample-math-001', stem='解方程：$x^2-5x+6=0$。请写出解题过程，并用代入法检验。',
                           knowledge_points=['一元二次方程', '因式分解'], answer='$x_1=2，x_2=3$').model_dump(),
         lesson=dict(title='从因式分解到方程的根', objective='理解零积原理，选择因式分解法求解，并养成验根习惯。',
             knowledge_points=[point('识别一元二次方程', '标准形式为 $ax^2+bx+c=0$，其中 $a\\ne0$。\n本题 $a=1$，$b=-5$，$c=6$。', '只含一个未知数，且未知数的最高次数为二的整式方程，叫做一元二次方程。先确认右边为零，再观察左边的结构。'),
                               point('因式分解与零积原理', '寻找两个数，使它们的和为 $-5$、积为 $6$。\n若 $AB=0$，则 $A=0$ 或 $B=0$。', '负二与负三的和是负五，积是六。把多项式写成两个一次因式的乘积后，至少有一个因式为零。这是因式分解法的依据。')],
             solution_steps=[point('第一步 · 分解左边', '$x^2-5x+6=(x-2)(x-3)$\n因此原方程化为 $(x-2)(x-3)=0$。', '展开右边检查：平方项是 x 的平方，交叉项相加为负五 x，常数项为六，与原式一致。'),
                             point('第二步 · 分别求根', '$x-2=0$ 或 $x-3=0$\n得到 $x_1=2$，$x_2=3$。', '不能只取一个因式等于零。两个一次方程分别给出两个不同的根。'),
                             point('第三步 · 代入检验', '当 $x=2$：$2^2-5\\times2+6=0$。\n当 $x=3$：$3^2-5\\times3+6=0$。', '将两个根分别代回原方程，左边均为零，所以两个解都符合题意。检验用原方程，而不是仅检查变形后的方程。')],
             answer='$x_1=2，x_2=3$。',
             pitfalls=[point('不要约去可能为零的因式', '从 $(x-2)(x-3)=0$ 直接约去 $(x-2)$，会丢失根 $x=2$。', '除法的前提是除数不为零。此处 x 减二可能为零，所以不能直接两边除以这个因式。')],
             summary=['先整理成右边为零的标准形式。', '分解因式后，令每个因式分别等于零。', '保留全部根，并代回原方程检验。'],
             variants=[dict(source='generated', question_id=None, stem='用因式分解法解方程：$x^2-7x+12=0$。', answer='$x=3$ 或 $x=4$。', analysis='因为 $-3+(-4)=-7$，$(-3)(-4)=12$，所以 $(x-3)(x-4)=0$，得到两个根。', knowledge_points=['因式分解']),
                       dict(source='generated', question_id=None, stem='解方程：$x^2-4x=0$，并解释为什么不能直接约去 $x$。', answer='$x=0$ 或 $x=4$。', analysis='提取公因式得 $x(x-4)=0$。直接约去 x 会丢失零根。', knowledge_points=['一元二次方程'])])),
    dict(question=Question(question_id='sample-physics-001', subject='物理', stem='一个阻值为 $10\\,\\Omega$ 的定值电阻，两端电压为 $6\\,\\mathrm{V}$。求通过它的电流；若电压变为 $3\\,\\mathrm{V}$，电流是多少？假设温度不变。', knowledge_points=['欧姆定律'], answer='$0.6\\,\\mathrm{A}$；$0.3\\,\\mathrm{A}$。').model_dump(),
         lesson=dict(title='用欧姆定律建立电压与电流的联系', objective='理解定值电阻的电流、电压关系，并正确代入单位。',
             knowledge_points=[point('欧姆定律的适用条件', '$I=\\frac{U}{R}$\n同一导体、温度等条件不变时，电流与电压成正比。', '题目明确给出了定值电阻与温度不变，因此两次计算中的阻值相同。不要把这个关系无条件推广到所有元件。'),
                               point('统一物理量的单位', '电压 $U$ 用伏特（V）；电阻 $R$ 用欧姆（Ω）；电流 $I$ 用安培（A）。', '先写公式，再代入带有单位的量。这样可以检查有没有把电压、电阻的位置颠倒。')],
             solution_steps=[point('求初始电流', '$I_1=\\frac{6}{10}=0.6\\,\\mathrm{A}$', '将六伏和十欧代入欧姆定律，通过电阻的电流为零点六安。'),
                             point('求改变电压后的电流', '$I_2=\\frac{3}{10}=0.3\\,\\mathrm{A}$', '温度不变，电阻仍为十欧。电压减半，电流也减半，可以用比例关系检验结果。')],
             answer='初始电流为 $0.6\\,\\mathrm{A}$；电压变为 3 V 后，电流为 $0.3\\,\\mathrm{A}$。',
             pitfalls=[point('电阻不会因代入数值而改变', '$R=\\frac{U}{I}$ 是计算关系；本题定值电阻始终为 $10\\,\\Omega$。', '不能认为电压减小电阻就减小。对题设的定值电阻，电流随着电压变化，而比值保持不变。')],
             summary=['读清同一导体与温度不变的条件。', '按 I=U/R 计算，并注明单位。', '电阻不变时，用电流与电压成正比检验。'],
             variants=[dict(source='generated', question_id=None, stem='一个 $20\\,\\Omega$ 的定值电阻接在 6 V 电源两端，求电流。', answer='$0.3\\,\\mathrm{A}$。', analysis='$I=6/20=0.3\\,\\mathrm{A}$。', knowledge_points=['欧姆定律']),
                       dict(source='generated', question_id=None, stem='同一导体温度不变，电压由 2 V 增大至 4 V，初始电流为 0.1 A，求新的电流。', answer='$0.2\\,\\mathrm{A}$。', analysis='电阻不变时电流与电压成正比，电压加倍，电流也加倍。', knowledge_points=['欧姆定律'])])),
    dict(question=Question(question_id='sample-chemistry-001', subject='化学', stem='在密闭容器中，4 g 氢气与 32 g 氧气恰好完全反应生成水。求生成水的质量，并说明依据。', knowledge_points=['质量守恒定律'], answer='36 g。', difficulty='基础').model_dump(),
         lesson=dict(title='在化学反应中追踪质量', objective='区分参加反应的质量与投入质量，用质量守恒定律求生成物质量。',
             knowledge_points=[point('质量守恒定律', '参加化学反应的各物质质量总和，等于反应后生成的各物质质量总和。', '化学反应前后，原子的种类和数目不变，因此总质量守恒。质量守恒并不意味着分子数目不变。'),
                               point('抓住“恰好完全反应”', '氢气与氧气均无剩余，生成物只有水。\n题目给出的 4 g 和 32 g 都是参加反应的质量。', '若一种物质过量，投入的总质量就不能直接全算作生成水的质量。本题明确恰好完全反应，因此可以直接相加。')],
             solution_steps=[point('列出守恒关系', '$m(\\text{水})=m(\\text{氢气})+m(\\text{氧气})$', '容器密闭，物质没有逸出或从外部进入；同时题目只生成水，因此用两种反应物参加反应的质量之和求水的质量。'),
                             point('代入并写出结果', '$m(\\text{水})=4+32=36\\,\\mathrm{g}$', '生成水的质量是三十六克。依据是质量守恒定律，答案需要带上质量单位。')],
             answer='生成水的质量为 36 g，依据质量守恒定律。',
             pitfalls=[point('守恒的是总质量', '不能把“质量守恒”误解为每一种物质的质量都不变。', '反应物逐渐减少，生成物逐渐增加；守恒的是所有参与反应物质组成系统的总质量。')],
             summary=['先确认哪些物质真正参加了反应。', '找出全部生成物，再列总质量关系。', '区分质量守恒与物质种类、分子数的变化。'],
             variants=[dict(source='generated', question_id=None, stem='2 g 氢气与 16 g 氧气恰好完全反应，生成水的质量是多少？', answer='18 g。', analysis='两种反应物都完全参加反应，生成水质量为 2+16=18 g。', knowledge_points=['质量守恒定律']),
                       dict(source='generated', question_id=None, stem='3 g 氢气与 16 g 氧气反应后，剩余 1 g 氢气，生成水的质量是多少？', answer='18 g。', analysis='实际参加反应的氢气为 3-1=2 g，水的质量为 2+16=18 g。', knowledge_points=['质量守恒定律'])])),
]

for entry in SAMPLES:
    entry['lesson'] = Lesson.model_validate(entry['lesson']).model_dump()


def get_sample(question):
    for entry in SAMPLES:
        if question.model_dump() == entry['question']:
            return deepcopy(entry['lesson'])
    raise ValueError('样例模式仅支持内置原题及原始教学条件。修改题目后请选择千问生成。')


def seed_resources():
    return [dict(id=f'sample-knowledge-{index}', title=p['title'], kind='knowledge',
                 subject=e['question']['subject'], grades=['九年级'], region='上海',
                 knowledge_points=e['question']['knowledge_points'], content=p['display']+'\n'+p['notes'],
                 source='内置教学样例（非官方教材）', version='1', is_sample=True, answer='', analysis='')
            for index, (e, p) in enumerate((e, p) for e in SAMPLES for p in e['lesson']['knowledge_points'])]

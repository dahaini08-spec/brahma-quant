import sys, os, json, time
from pathlib import Path
sys.path.insert(0, '.')

SIGNAL_LOG_PATH = Path('data/live_signal_log.jsonl')
LOG_PATH = Path('data/auto_executor_log.jsonl')

now_ts = time.time()
if SIGNAL_LOG_PATH.exists():
    all_sigs = []
    for line in open(SIGNAL_LOG_PATH):
        line = line.strip()
        if not line: continue
        try:
            s = json.loads(line)
            if s.get('valid'):
                all_sigs.append(s)
        except:
            pass
    recent = [s for s in all_sigs if (now_ts - float(s.get('ts', 0) or 0)) < 4*3600]
    print("有效信号总数:", len(all_sigs))
    print("近4小时有效信号:", len(recent))
    for s in recent[-5:]:
        ts_str = time.strftime('%H:%M', time.gmtime(float(s.get('ts',0))))
        print(" ", s.get('symbol'), s.get('signal_dir') or s.get('direction'), 
              "score="+str(s.get('score')), "regime="+str(s.get('regime')), "ts="+ts_str)
else:
    print('无信号文件')

if LOG_PATH.exists():
    paper = []
    for line in open(LOG_PATH):
        line = line.strip()
        if not line: continue
        try:
            l = json.loads(line)
            if l.get('event') == 'PAPER_PENDING' or '纸面' in str(l.get('reason','')):
                paper.append(l)
        except:
            pass
    recent_paper = [p for p in paper if (now_ts - float(p.get('ts', 0) or 0)) < 2*3600]
    print("近2小时纸面推送:", len(recent_paper))
    for p in recent_paper:
        ts_str = time.strftime('%H:%M', time.gmtime(float(p.get('ts',0))))
        print(" ", p.get('symbol'), p.get('direction'), "score="+str(p.get('score')), "ts="+ts_str)

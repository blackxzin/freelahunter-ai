import argparse, os, sys
from pathlib import Path; sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from freelahunter.core import *

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--provider',default='mock'); args=ap.parse_args()
    reg=ProviderRegistry(); reg.register('mock',MockProvider()); reg.register('authorized_mock',AuthorizedMockProvider())
    provider=reg.get(args.provider); db=Database()
    config=Config()
    s=run_pipeline(provider,db,auto_send=config.auto_send,dry_run=config.dry_run,kill_switch=config.kill_switch,min_score=config.min_score,min_confidence=config.min_confidence,max_per_hour=config.max_hour,max_per_day=config.max_day,pause_no_response_days=config.pause_no_response_days,pause_consecutive_no_response=config.pause_consecutive_no_response,pause_rejection_threshold=config.pause_rejection_threshold)
    print(s)
if __name__=='__main__': main()

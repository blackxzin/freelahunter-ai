import threading, time
from .core import Config, Database, ProviderRegistry, NotificationService, CircuitBreaker, run_pipeline

class HunterScheduler:
    """Periodic runner; APScheduler can wrap this class when installed."""
    def __init__(self, registry, db=None, config=None, notifier=None, runtime_control=None):
        self.registry=registry; self.db=db or Database(); self.config=config or Config(); self.notifier=notifier or NotificationService(); self.breaker=CircuitBreaker(); self.running=False; self._lock=threading.Lock(); self.runtime_control=runtime_control
    def run_cycle(self):
        if not self._lock.acquire(blocking=False): return {'skipped':True}
        self.running=True; result={}
        try:
            for provider in self.registry.enabled():
                if not self.breaker.allow(provider.name): result[provider.name]={'error':'cooldown'}; continue
                try:
                    result[provider.name]=run_pipeline(provider,self.db,auto_send=self.config.auto_send,dry_run=self.config.dry_run,kill_switch=self.config.kill_switch,runtime_control=self.runtime_control,min_score=self.config.min_score,min_confidence=self.config.min_confidence,max_per_hour=self.config.max_hour,max_per_day=self.config.max_day,pause_no_response_days=self.config.pause_no_response_days,pause_consecutive_no_response=self.config.pause_consecutive_no_response,pause_rejection_threshold=self.config.pause_rejection_threshold)
                    self.breaker.record_success(provider.name)
                    if result[provider.name].get('paused'):
                        self.notifier.notify('AUTOMATION_PAUSED', {'reason': result[provider.name].get('pause_reason')})
                except Exception as exc: self.breaker.record_failure(provider.name); result[provider.name]={'error':str(exc)}; self.notifier.notify('PROVIDER_ERROR',{'provider':provider.name,'error':str(exc)})
            return result
        finally: self.running=False; self._lock.release()
    def start(self, once=False):
        while True:
            self.run_cycle()
            if once: return
            time.sleep(self.config.interval*60)

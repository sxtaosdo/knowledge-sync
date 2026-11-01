"""Internal scheduler ticks only. No OS tasks, hooks, services, implicit fetch or autostart."""
import time


class Scheduler:
    def __init__(self,service,*,clock=time.time):
        self.service,self.clock=service,clock
        self.next_due={}

    def tick(self):
        now=self.clock()
        results=[]
        for key,source in self.service.registry.sources.items():
            if not source.schedule_enabled or source.interval_seconds<60:
                continue
            if now<self.next_due.get(key,0):
                continue
            self.next_due[key]=now+source.interval_seconds
            try:
                # Gates are rechecked on every tick; expiry cannot inherit an earlier enable.
                source.require_writer(now)
                results.append(dict(sourceId=source.source_id,channel=source.binding.channel,
                    ok=True,data=self.service.enqueue(source)))
            except Exception:
                results.append(dict(sourceId=source.source_id,channel=source.binding.channel,
                    ok=False,error='SCHEDULE_BLOCKED'))
        return results

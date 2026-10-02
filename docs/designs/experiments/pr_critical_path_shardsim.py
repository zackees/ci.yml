"""Experiment E5 (zackees/ci.yml#189): simulate soldr nextest wall time under
N-way sharding and with the remote-only per-test delay removed, from one CI
run's per-test durations (`<binary>|<test> <secs>` lines; argv[1] = dir holding
ciall.t). Calibrated: N=1 simulates 483 s vs 480 s measured (run 36954283026)."""
import sys,re,heapq,hashlib
S=sys.argv[1]
tests=[]
for line in open(f'{S}/ciall.t'):
    key,secs=line.split(); b,t=key.split('|'); tests.append((b,t,float(secs)))
cold_mods='cli_cargo_basic|cli_cargo_linker|cli_cargo_run_trampoline|cli_cargo_wrappers|cli_dylint_wrapper'
def group(b,t):
    if b=='soldr-cli::cargo_front_door' and (re.match(rf'^({cold_mods})::',t) or t.startswith('cli_cargo_doc_routes::')): return 'cold'
    if b.endswith('::cache_gc') and t.startswith('agent_worktree_share::'): return 'rt'
    if b.endswith('::daemon') and re.match(r'^(cli_daemon_builds|cli_daemon_flush_caches|cli_daemon_lifecycle|cli_daemon_single_instance|cli_daemon_target_touch|daemon_cache_maintenance|daemon_restart_warmth|daemon_rss_ceiling)::',t): return 'rt'
    if b.endswith('::broker') and re.match(r'^(cli_broker_purge|cli_broker_resurrection|cli_broker_routes|cli_broker_single_instance|cli_broker_status|cli_broker_stop|cli_build_alias_parity|cli_build_fetch_overlap|cli_jobs_routing|cli_kill_matrix)::',t): return 'rt2'
    return ''
caps={'cold':2,'rt':2,'rt2':2}
def sim(ts,threads=4):
    pending=sorted(ts,key=lambda x:-x[2]); active=[]; time=0.0
    while pending or active:
        started=True
        while started and len(active)<threads:
            started=False
            for i,(b,tn,d) in enumerate(pending):
                g=group(b,tn)
                if g and sum(1 for f,gg in active if gg==g)>=caps[g]: continue
                heapq.heappush(active,(time+d,g)); pending.pop(i); started=True; break
        f,g=heapq.heappop(active); time=f
    return time
def fixed(ts): return [(b,t,max(d-12.0,d*0.15) if b=='soldr-cli::cargo_front_door' else d) for b,t,d in ts]
print('N=1', round(sim(tests)), '| delay removed', round(sim(fixed(tests))))
for n in (2,3,4):
    sh=[[] for _ in range(n)]
    for x in tests: sh[int(hashlib.md5((x[0]+x[1]).encode()).hexdigest(),16)%n].append(x)
    shf=[[] for _ in range(n)]
    for x in fixed(tests): shf[int(hashlib.md5((x[0]+x[1]).encode()).hexdigest(),16)%n].append(x)
    print(f'N={n} max shard', round(max(sim(s) for s in sh)), '| delay removed', round(max(sim(s) for s in shf)))
print('cold test-s', round(sum(d for b,t,d in tests if group(b,t)=='cold')))

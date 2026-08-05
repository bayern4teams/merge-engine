# Scoring profile — Part 1.5 A.0

## technology (1503 records) — 59.09s

```
         55470381 function calls (55435367 primitive calls) in 61.525 seconds

   Ordered by: cumulative time
   List reduced from 7125 to 25 due to restriction <25>

   ncalls  tottime  percall  cumtime  percall filename:lineno(function)
        1    0.000    0.000   61.814   61.814 D:\transfer\merging\scripts\diagnostics\profile_resolve.py:134(_target)
        1    0.000    0.000   61.814   61.814 C:\Users\rezas\miniconda3\envs\na180\Lib\asyncio\runners.py:160(run)
        3    0.000    0.000   61.811   20.604 C:\Users\rezas\miniconda3\envs\na180\Lib\asyncio\base_events.py:618(run_until_complete)
        3    0.000    0.000   61.810   20.603 C:\Users\rezas\miniconda3\envs\na180\Lib\asyncio\windows_events.py:317(run_forever)
        3    0.000    0.000   61.810   20.603 C:\Users\rezas\miniconda3\envs\na180\Lib\asyncio\base_events.py:594(run_forever)
        1    0.000    0.000   61.810   61.810 C:\Users\rezas\miniconda3\envs\na180\Lib\asyncio\runners.py:86(run)
       81    0.002    0.000   61.810    0.763 C:\Users\rezas\miniconda3\envs\na180\Lib\asyncio\base_events.py:1859(_run_once)
       90    0.000    0.000   61.806    0.687 C:\Users\rezas\miniconda3\envs\na180\Lib\asyncio\events.py:82(_run)
       90    0.002    0.000   61.805    0.687 {method 'run' of '_contextvars.Context' objects}
       68    0.025    0.000   61.801    0.909 D:\transfer\merging\scripts\diagnostics\profile_resolve.py:121(_run_one)
       68    0.250    0.004   59.056    0.868 D:\transfer\merging\src\mcp_server\tools\resolve_entities.py:254(resolve_entities_impl)
        1    0.001    0.001   40.853   40.853 D:\transfer\merging\src\mcp_server\tools\resolve_entities.py:1054(_fit_match_model)
    51563    0.341    0.000   28.804    0.001 D:\transfer\merging\src\mcp_server\tools\resolve_entities.py:1418(_build_comparison_vector)
        1    0.125    0.125   28.629   28.629 D:\transfer\merging\src\mcp_server\tools\resolve_entities.py:1099(<listcomp>)
    51563    0.502    0.000   27.126    0.001 D:\transfer\merging\src\mcp_server\tools\resolve_entities.py:1436(<dictcomp>)
   206252    1.079    0.000   26.517    0.000 D:\transfer\merging\src\mcp_server\tools\resolve_entities.py:1374(_comparison_level)
   274186    0.772    0.000   23.924    0.000 D:\transfer\merging\src\mcp_server\tools\resolve_entities.py:1001(_string_similarity)
    53663    1.609    0.000   22.995    0.000 D:\transfer\merging\src\mcp_server\tools\resolve_entities.py:1015(_string_similarity_cached)
   100359    0.458    0.000   19.888    0.000 D:\transfer\merging\src\mcp_server\tools\resolve_entities.py:967(_seq_ratio)
   100359    0.571    0.000   16.100    0.000 C:\Users\rezas\miniconda3\envs\na180\Lib\difflib.py:597(ratio)
   100359    2.418    0.000   14.989    0.000 C:\Users\rezas\miniconda3\envs\na180\Lib\difflib.py:421(get_matching_blocks)
    47705    0.476    0.000   14.328    0.000 D:\transfer\merging\src\mcp_server\tools\resolve_entities.py:1123(_score_pair)
        1    1.659    1.659   12.067   12.067 D:\transfer\merging\src\mcp_server\tools\fellegi_sunter.py:182(estimate_model)
   429345    0.602    0.000   11.651    0.000 D:\transfer\merging\src\mcp_server\tools\fellegi_sunter.py:128(probability)
   387280    9.168    0.000   11.453    0.000 C:\Users\rezas\miniconda3\envs\na180\Lib\difflib.py:305(find_longest_match)



```

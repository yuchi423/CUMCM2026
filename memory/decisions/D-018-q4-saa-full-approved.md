# D-018 问题4 SAA正式运行批准

日期：2026-09-12

用户在HUMAN MODEL GATE明确批准“冻结当前方案，运行334天”。据此冻结 `saa_load` 的14日历史供需场景、问题二 `combined` 供需预测、0.8分位余量、储能参数、5倍紧急购电价格、名义层0--1互斥和实际因果执行口径。正式运行不得根据结果调整参数。

比较对象固定为 `mean7`、`mean14`、`fixed` 和不参与选择的 `oracle_actual`；失败时回退 `mean7`。正式裁决条件见 `planning/q4-saa-full-protocol.md`。

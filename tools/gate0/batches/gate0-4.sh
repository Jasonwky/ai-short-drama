#!/usr/bin/env bash
# 闸门 0-4:参考音频能否锁住音色。B/C 带参考音频,B'/C' 同台词同种子不带 —— 唯一变量是有无锁音色。
cd /d/work/game/ai-short-drama
FRAME="outputs/gate0/0-3v-fast_fast_20260923-095420/0-3v-fast_00001_.png"
VOICE=outputs/gate0/_refs/voice_A.wav
SCENE="The pink-haired woman in gold armor stands in the dusk courtyard holding the jade pendant, static camera, medium shot, natural ambient sound, no music."
LINE_B='"我等了他整整三年，他却再也没有回来。"'
LINE_C='"如果你真的在乎我，就把真相告诉我。"'
# 不在源头过滤输出:0-4 首跑时 `| grep` 把执行器的 Traceback 吃掉了,C 半路崩掉却没人知道。
# 完整输出留给监控端去筛。
run () { python -X utf8 -u -m tools.gate0.run_video --preset baseline --seconds 8 "$@" 2>&1 || echo "[失败] 执行器退出码 $?"; }
run --gate 0-4-B  --seed 1001 --image "$FRAME" --audio "$VOICE" --prompt "<Picture 1> is the first frame. $SCENE She speaks in Mandarin Chinese with exactly the same voice, timbre and accent as the speaker in <Audio 1>: $LINE_B"
run --gate 0-4-C  --seed 1002 --image "$FRAME" --audio "$VOICE" --prompt "<Picture 1> is the first frame. $SCENE She speaks in Mandarin Chinese with exactly the same voice, timbre and accent as the speaker in <Audio 1>: $LINE_C"
run --gate 0-4-Bx --seed 1001 --image "$FRAME" --prompt "<Picture 1> is the first frame. $SCENE She speaks in Mandarin Chinese: $LINE_B"
run --gate 0-4-Cx --seed 1002 --image "$FRAME" --prompt "<Picture 1> is the first frame. $SCENE She speaks in Mandarin Chinese: $LINE_C"
echo "===== 0-4 全部结束 ====="

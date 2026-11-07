#!/bin/bash
# usage: run-eval.sh <tag> [extra opencode args...]
S=$SCRATCH; tag=$1; shift
cd ~/Code; for d in */; do git -C "$d" status --porcelain 2>/dev/null | sed "s|^|$d |"; done > $S/runs/$tag.git-before
while IFS='|' read -r id p; do
  export RUN_ID=$tag-$id; start=$(date +%s)
  PATH=$S/shim:$PATH setsid timeout -k 10 900 opencode run --dir ~/Code --format json "$@" "$p" </dev/null > $S/runs/$RUN_ID.json 2> $S/runs/$RUN_ID.err
  echo "$RUN_ID rc=$? secs=$(( $(date +%s)-start ))" >> $S/runs/$tag.summary
done < $S/prompts.txt
for d in */; do git -C "$d" status --porcelain 2>/dev/null | sed "s|^|$d |"; done > $S/runs/$tag.git-after
diff $S/runs/$tag.git-before $S/runs/$tag.git-after > $S/runs/$tag.git-diff; echo DONE >> $S/runs/$tag.summary

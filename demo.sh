#!/usr/bin/env bash
# Runs both checks on the two example summaries of the same teleconsult.
cd "$(dirname "$0")/guardrails" || exit 2
E=../examples/teleconsult
for doc in doc_faithful.md doc_hallucinated.md; do
  echo "== $doc"
  python3 rastreia_campos.py --fonte $E/source.txt --transcricao $E/transcript.md $E/$doc
  python3 lint_4d.py $E/$doc --final
  echo
done

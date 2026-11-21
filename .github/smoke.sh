#!/usr/bin/env bash
# End-to-end checks of the command line, used by CI after the unit tests.
set -euxo pipefail
work="$(mktemp -d)"
libra train data --out "$work/ckpt" --steps 40 --eval-every 20 --vocab 400 --ctx 32 --layers 1 --embd 32 --heads 2
libra generate --model "$work/ckpt" --prompt "The" --max-tokens 10
libra data data --out "$work/ds"
libra train "$work/ds" --out "$work/ckpt2" --steps 5 --eval-every 5 --vocab 400 --ctx 32 --layers 1 --embd 32 --heads 2
libra skill list --plugins plugins
libra skill run calc "(2+3)*4"
export LIBRA_HOME="$work/libra-home"
libra mcp add fake -- python tests/fixtures/fake_mcp_server.py
libra skill find echo
libra skill run mcp.fake.echo hello
libra trust list
libra translate add examples/phrasebook_en_es.tsv --src en --tgt es
libra translate align --src en --tgt es
libra translate text "Where is the hotel?" --to es
./deploy/install-debian.sh --root "$work/install-root" --port 8799

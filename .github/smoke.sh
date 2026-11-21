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
libra agent plan "what is (2+3)*4 then translate it to Spanish"
libra agent run "what is (2+3)*4"
libra agent add sum "what is 6*7" --every hourly
libra agent run-due
libra pair --url http://192.0.2.1:8000 --token-file /dev/null
libra memory add "the backup runs at 3am"
libra memory search backup
libra agent auto "what is (2+3)*4"
libra agent procedures
libra discord config
./deploy/install-debian.sh --root "$work/install-root" --port 8799
./deploy/install-debian.sh --root "$work/install-root" --tailscale --discord --port 8799 </dev/null

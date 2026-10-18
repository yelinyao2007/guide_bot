#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
revision=62ab00f2bc44f6f20c1c9b7a13f3fb1dd41a4e35
if [ -d assets/menagerie ]; then
  actual=$(git -C assets/menagerie rev-parse HEAD)
  if [ "$actual" != "$revision" ]; then
    echo "Existing Menagerie checkout has a different revision; preserve it and use MUJOCO_MODEL explicitly." >&2
    exit 1
  fi
else
  git clone --filter=blob:none --no-checkout https://github.com/google-deepmind/mujoco_menagerie.git assets/menagerie
  git -C assets/menagerie sparse-checkout init --cone
  git -C assets/menagerie sparse-checkout set unitree_go2
  git -C assets/menagerie checkout --detach "$revision"
fi
test -f assets/menagerie/unitree_go2/scene.xml
echo "Menagerie Go2 ready: $revision (Vbot simulation substitute)"

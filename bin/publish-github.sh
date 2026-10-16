#!/usr/bin/env bash
# Build / refresh the GitHub copy of this project: a SEPARATE folder that contains only what a user or
# developer needs, with the user guide's landing page as its top-level README.md. The full working copy
# (with HANDOFF.md, PLAN.md, FLOW.md, TEAM_ROLLOUT.md, SHARING.md, PROJECT_STATEMENT.md, logs, ...) stays here
# on the device untouched.
#
#   bash bin/publish-github.sh            # sync allow-listed files into $DEST, commit there, show what to push
#   bash bin/publish-github.sh --push     # ... and push $DEST to the GitHub remote
#
# DEST (default /home/<you>.scratch/mywork/pd_flow_airflow_github) is a git repo of its own whose only remote
# is GitHub. Edit code and docs HERE, run this script, and the GitHub copy follows.
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${PDFLOW_GITHUB_DIR:-$(dirname "$SRC")/pd_flow_airflow_github}"
REMOTE_URL="${PDFLOW_GITHUB_URL:-git@github.com:jayadityasingh9/pd_flow_airflow.git}"
PUSH=0; [ "${1:-}" = "--push" ] && PUSH=1

# ---- what goes to GitHub (everything else stays local) -------------------------------------------
INCLUDE=(
  docs/user-guide/01-prerequisites.md docs/user-guide/02-passwordless-ssh.md docs/user-guide/03-setup-and-start.md
  docs/user-guide/04-access-the-ui.md docs/user-guide/05-run-a-flow.md docs/user-guide/06-troubleshooting.md
  docs/user-guide/07-faq.md docs/user-guide/08-maintainers.md
  airflow/env.sh airflow/setup.sh airflow/start.sh airflow/stop.sh airflow/status.sh airflow/af.sh
  airflow/watchdog.sh airflow/ui-tunnel.sh airflow/local.env.example
  airflow/dags/raven_pd_flow_dag.py
  pdflow/__init__.py pdflow/airflow_ops.py pdflow/buildsrc.py pdflow/checks.py pdflow/cli.py pdflow/config.py
  pdflow/context.py pdflow/doctor.py pdflow/patch.py pdflow/precheck.py pdflow/qor.py pdflow/remote.py
  pdflow/runner.py pdflow/scripts.py pdflow/summary.py pdflow/workspace.py
  bin/pdflow.sh bin/pdflow-user-setup.sh bin/publish-github.sh
  config/fixed.yaml config/local.yaml.example
  tests/test_pdflow.py
  .gitignore
)
INCLUDE_DIRS=( tests/fixtures )          # copied whole (small reference logs/reports the tests need)

# ---- destination repo ------------------------------------------------------------------------------
if [ ! -d "$DEST/.git" ]; then
  echo "-- creating $DEST (git clone of this checkout's history, remote = GitHub)"
  git clone -q "$SRC" "$DEST"
  git -C "$DEST" remote remove origin
  git -C "$DEST" remote add github "$REMOTE_URL"
fi

# ---- sync: remove everything tracked except .git, then copy the allow-list -------------------------
( cd "$DEST" && git ls-files -z | xargs -0 rm -f -- 2>/dev/null; find . -mindepth 1 -type d -empty -not -path './.git*' -delete )
for f in "${INCLUDE[@]}"; do
  mkdir -p "$DEST/$(dirname "$f")"; cp -p "$SRC/$f" "$DEST/$f"
done
for d in "${INCLUDE_DIRS[@]}"; do
  mkdir -p "$DEST/$d"; cp -rp "$SRC/$d/." "$DEST/$d/"
done
# the guide's landing page becomes the top-level README; its links move one level up
sed -E 's#\]\((0[1-8]-[a-z-]+\.md)\)#](docs/user-guide/\1)#g' "$SRC/docs/user-guide/README.md" \
  | sed -E 's#Operator reference: `\.\./\.\./README\.md`; how the DAG works inside: `\.\./\.\./FLOW\.md`\.##' > "$DEST/README.md"
# no mention of files that are not in this copy
sed -i -E 's#`SHARING\.md` way E says no; an internal GitHub#keep it private on the MIPS GitHub; an internal GitHub#' "$DEST/docs/user-guide/08-maintainers.md"
sed -i -E 's#\(see SHARING\.md / TEAM_ROLLOUT\.md\)#(see README.md)#; s#\(SHARING\.md way B: #(demo-only intranet UI: #' "$DEST/airflow/env.sh"
sed -i -E 's#\(SHARING\.md way B, demo only#(demo only#' "$DEST/airflow/local.env.example"

# ---- commit --------------------------------------------------------------------------------------
cd "$DEST"
git add -A
if git diff --cached --quiet; then
  echo "-- GitHub copy already up to date with $(git -C "$SRC" rev-parse --short HEAD)"
else
  git commit -q -m "sync from working copy $(git -C "$SRC" rev-parse --short HEAD): $(git -C "$SRC" log -1 --format=%s | cut -c1-70)"
  echo "-- committed $(git rev-parse --short HEAD) in $DEST ($(git ls-files | wc -l) files)"
fi
echo "-- leftovers check:"; grep -rlE 'HANDOFF\.md|PLAN\.md|PROJECT_STATEMENT\.md|FLOW\.md|TEAM_ROLLOUT\.md|SHARING\.md' --include='*.md' --include='*.sh' --include='*.example' --exclude=publish-github.sh . 2>/dev/null | sed 's/^/   still mentions a local-only doc: /' || echo "   none"
if [ "$PUSH" = 1 ]; then
  git push -u github main
  echo "-- pushed to $REMOTE_URL"
else
  echo "-- not pushed. To publish:  cd $DEST && git push -u github main     (or re-run with --push)"
fi

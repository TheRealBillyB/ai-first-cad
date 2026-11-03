#!/bin/bash
# Double-click this file on a Mac. It runs the installer in this folder.
cd "$(dirname "$0")" || exit 1
exec /bin/bash ./install.sh

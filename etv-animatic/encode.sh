#!/usr/bin/env bash
# Кадры аниматика → mp4 по роликам + общий шоурил.
# Использование: ./encode.sh <папка_с_кадрами> <папка_для_видео>
set -euo pipefail
frames=${1:?папка с кадрами}
out=${2:?папка для видео}
mkdir -p "$out"
declare -A names=([01_Заставка]=ETV_animatic_01_zastavka [02_Вердикт]=ETV_animatic_02_verdikt
                  [03_Гаджетотека]=ETV_animatic_03_gadzhetoteka [04_Отбивка]=ETV_animatic_04_otbivka)
list=$(mktemp)
for scene in 01_Заставка 02_Вердикт 03_Гаджетотека 04_Отбивка; do
  mp4="$out/${names[$scene]}.mp4"
  ffmpeg -loglevel error -y -framerate 30 -start_number 1 -i "$frames/$scene/%04d.png" \
    -c:v libx264 -preset slow -crf 20 -pix_fmt yuv420p -movflags +faststart "$mp4"
  echo "file '$(realpath "$mp4")'" >> "$list"
done
ffmpeg -loglevel error -y -f concat -safe 0 -i "$list" -c copy -movflags +faststart "$out/ETV_animatic_showreel.mp4"
rm -f "$list"
ls -la "$out"

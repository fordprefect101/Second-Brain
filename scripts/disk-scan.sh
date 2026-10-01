#!/bin/zsh
# Read-only disk scan: measures what takes space, deletes nothing.
# Writes ~/Desktop/disk-scan.txt. See docs/m1-setup.md.

{
echo "== Disk"; df -h / | tail -1
echo "== Home folders"; du -sh ~/* 2>/dev/null | sort -h | tail -15
echo "== Library"; du -sh ~/Library/* 2>/dev/null | sort -h | tail -15
echo "== Caches"; du -sh ~/Library/Caches/* 2>/dev/null | sort -h | tail -20
echo "== App Support"; du -sh ~/Library/Application\ Support/* 2>/dev/null | sort -h | tail -20
echo "== App containers"; du -sh ~/Library/Containers/* ~/Library/Group\ Containers/* 2>/dev/null | sort -h | tail -15
echo "== Developer"; du -sh ~/Library/Developer/* ~/.npm ~/.cache ~/.docker ~/Library/Caches/Homebrew 2>/dev/null | sort -h
echo "== iPhone backups"; du -sh ~/Library/Application\ Support/MobileSync/Backup 2>/dev/null
echo "== Trash"; du -sh ~/.Trash 2>/dev/null
echo "== Files over 1 GB"; find ~ -type f -size +1G 2>/dev/null | head -30
echo "== node_modules folders"
find ~ -name node_modules -type d -prune 2>/dev/null | head -20 | while read -r d; do du -sh "$d"; done
} > ~/Desktop/disk-scan.txt 2>&1

echo "Done: disk-scan.txt is on the Desktop"

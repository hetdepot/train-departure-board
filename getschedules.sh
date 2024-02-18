#!/usr/bin/env bash

cd "$(dirname "$0")"
timeout -k 60 45 python3 get-current-departures.py
cp pages/*.html /var/www/html/schedules/

#!/usr/bin/env bash

cd "$(dirname "$0")"
python3 get-current-departures.py
cp pages/*.html /var/www/html/schedules/

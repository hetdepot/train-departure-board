#!/usr/bin/env bash

cd "$(dirname "$0")"
cd rawdata
wget $SCHEDULE_URL -O rawdata.zip
unzip -o rawdata.zip
rm rawdata.zip

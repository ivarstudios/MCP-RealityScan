#!/usr/bin/env python3
"""Fake RealityScan.exe for tests: echoes argv, honours a few commands.
  -exportGlobalSettings <f>      writes a tiny rcconfig
  -exportReconstructionRegion <f> writes a tiny rsbox
  -exportReport <out> <tpl>      writes a report
  -sleep <s>                     (non-RS) sleeps, to test background jobs
  -fail                          (non-RS) exit 3
"""
import sys, time, os
argv = sys.argv[1:]
print("STUB argv:", argv)
i = 0
rc = 0
while i < len(argv):
    a = argv[i]
    if a == "-exportGlobalSettings":
        open(argv[i+1], "w").write('<?xml version="1.0"?><Settings TexturingColorCorrection="1" TexturingExposure="0"/>'); i += 2; continue
    if a == "-exportReconstructionRegion":
        open(argv[i+1], "w").write('<?xml version="1.0"?><ReconstructionRegion isGeoreferenced="1"><yawPitchRoll>0 0 0</yawPitchRoll><widthHeightDepth>100 100 50</widthHeightDepth><Header><CentreEuclid><centre>1 2 3</centre></CentreEuclid></Header></ReconstructionRegion>'); i += 2; continue
    if a == "-exportReport":
        open(argv[i+1], "w").write("REPORT from " + argv[i+2]); i += 3; continue
    if a == "-sleep":
        time.sleep(float(argv[i+1])); i += 2; continue
    if a == "-fail":
        rc = 3
    if a == "-getStatus":
        print("STATUS instance", argv[i+1], "idle"); i += 2; continue
    i += 1
print("STUB done rc", rc)
sys.exit(rc)

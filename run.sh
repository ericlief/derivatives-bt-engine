#!/bin/bash

LOG_LEVEL=DEBUG tsmom --instruments 'MES,MNQ,MCL,MZL,MZC,MZS,MZW,MGC,SIL,J7,BRE,6M' --max-contracts 2 --max-notional 75000 --client-id 15

LOG_LEVEL=DEBUG tsmom --instruments 'ES,NQ,CL,ZL,ZC,ZS,ZW,GC,SI,JPY,BRE,6M' --max-contracts 4 --max-notional 750000 --client-id 15

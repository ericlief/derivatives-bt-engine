#!/bin/bash

LOG_LEVEL=DEBUG tsmom --instruments 'MES,MNQ,MCL,MZL,MZC,MZS,MZW,MGC,SIL,TN,J7,BRE,6M' --max-contracts 15 --max-notional 75_000 \
    --client-id 15 --account-equity 80_000 --min-conviction 0.5

LOG_LEVEL=DEBUG tsmom --instruments 'ES,NQ,CL,ZL,ZC,ZS,ZW,GC,SI,ZN,JPY,BRE,6M' --max-contracts 30 --max-notional 75_0000 \
    --client-id 15 --account-equity 187_000 --min-conviction 0.5 

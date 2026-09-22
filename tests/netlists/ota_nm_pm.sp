* 金标准用例 1：真实命名风格（NM1/PM1）+ 两级 Miller 运放
* 回归目标：器件类型不能靠首字母判断——原实现会把整张网表的 MOS 全部丢掉
.subckt OTA_NM vinp vinn vout vdd vss ibias
NM1 net1 vinp net3 vss nmos_rf w=10u l=1u m=1
NM2 net2 vinn net3 vss nmos_rf w=10u l=1u m=1
NM3 net3 ibias vss vss nmos_rf w=5u l=1u m=1
PM1 net1 net1 vdd vdd pmos_rf w=20u l=1u m=1
PM2 net2 net1 vdd vdd pmos_rf w=20u l=1u m=1
NM4 vout net2 vss vss nmos_rf w=30u l=1u m=1
CC vout net2 vss 3p
.ends OTA_NM

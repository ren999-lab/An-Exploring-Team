* 金标准用例 6：双差分对（两级各一个输入对）
* 回归目标：原实现只返回第一组差分对，第二对 M3/M4 完全不会被识别
.subckt DUAL v1 v2 v3 v4 o1 vdd vss
M1 a  v1 t1 vss nmos w=4u l=1u m=1
M2 b  v2 t1 vss nmos w=4u l=1u m=1
M3 c  v3 t2 vss nmos w=4u l=1u m=1
M4 d  v4 t2 vss nmos w=4u l=1u m=1
M5 t1 nb vss vss nmos w=8u l=1u m=1
M6 t2 nb vss vss nmos w=8u l=1u m=1
M7 o1 nb vss vss nmos w=8u l=1u m=1
M8 nb nb vss vss nmos w=2u l=1u m=1
.ends DUAL

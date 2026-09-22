* 金标准用例 2：层次网表（BIAS 子电路 + AMP 子电路）
* 回归目标：不能跨 .subckt 把器件"缝合"成并不存在的电流镜
*           （原实现会把 MB1/MB2 与 MA3 判成同一个电流镜，并生成错误约束）
.subckt BIAS ibias vdd vss
MB1 ibias ibias vss vss nmos w=2u l=1u m=1
MB2 nbias ibias vss vss nmos w=6u l=1u m=3
.ends BIAS

.subckt AMP inp inn out vdd vss
MA1 d1 inp t vss nmos w=2u l=1u m=1
MA2 d2 inn t vss nmos w=2u l=1u m=1
MA3 t nbias vss vss nmos w=4u l=1u m=1
MA4 d1 d1 vdd vdd pmos w=4u l=1u m=1
MA5 d2 d1 vdd vdd pmos w=12u l=1u m=1
.ends AMP

* 金标准用例 3：镜像比分别用 W 和 m 表达
* 回归目标：
*   * M5/M4 用 W 表达 3 倍镜像比 -> 必须保留 =M4_w*3（原实现会静默改成 1:1）
*   * M7/M6 用 m 表达 4 倍镜像比 -> 必须保留 =M6_m*4
*   * 约减网表里不能出现 {M4_w*3_w} 这种非法变量名
.subckt MIRROR vdd vss inp inn out
M1 d1 inp t vss nmos w=10u l=1u m=1
M2 d2 inn t vss nmos w=10u l=1u m=1
M3 t nb vss vss nmos w=10u l=1u m=1
M4 d1 d1 vdd vdd pmos w=4u l=1u m=1
M5 d2 d1 vdd vdd pmos w=12u l=1u m=1
M6 b1 b1 vdd vdd pmos w=4u l=1u m=2
M7 out b1 vdd vdd pmos w=4u l=1u m=8
.ends MIRROR

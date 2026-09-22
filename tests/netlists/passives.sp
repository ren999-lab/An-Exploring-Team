* 金标准用例 4：无源器件取值与多端节点
* 回归目标：原实现把 `R1 a b 5k` 的阻值、`CC1 a b c 1p` 的第三节点与容值全部丢弃
*           R/C 有 W/L/Seg 时按 PDK 参数化；只有裸值时把值本身作为变量
.subckt PASSIVES a b vdd vss
M1 d1 a t vss nmos w=10u l=1u m=1
M2 d2 b t vss nmos w=10u l=1u m=1
M3 t nb vss vss nmos w=10u l=1u m=1
R1 d1 b 5k
R2 d2 vss 3k seg=2 w=2u l=10u
CC1 d1 d2 vss 1p
C2 d1 vss 1p w=2u l=5u
.ends PASSIVES

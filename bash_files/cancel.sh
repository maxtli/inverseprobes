JOBS=(
33066121
33066122
33066170
33066171
33066172
33066174
33066175
33066188
33066189
33066191
33066192
33066193
33066208
33066209
33066210
33066211
33066212
33066213
33066249
33066251
33066252
33066254
33066255
33066256
33066279
33066280
33066281
33066282
33066283
33066284
)
for x in ${JOBS[@]}
do
scancel $x
done 
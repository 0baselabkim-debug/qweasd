"""Presentation only; chart gaps and CSV retain unknown observations."""
import altair as alt
import streamlit as st


def chart(frame,period,key,title):
    f=frame.tail(period).copy()
    f['cumulative_amount']=f.net_amount.cumsum(skipna=False)
    f['거래일']=f.trade_date.dt.strftime('%Y-%m-%d')
    f['일별 순매수(억원)']=f.net_amount/1e8;f['누적 순매수(억원)']=f.cumulative_amount/1e8
    f['수신/대상']=f.observed_members.astype(str)+' / '+f.expected_members.astype(str)
    if title:st.markdown('**'+title+'**')
    mode=st.radio('차트 표시', ['일별 순매수','누적 순매수','함께 보기'],horizontal=True,key=key+'_mode',label_visibility='collapsed')
    base=alt.Chart(f).encode(x=alt.X('거래일:O',sort=None,axis=alt.Axis(labelAngle=-35,labelExpr="slice(datum.label, 5)"),title=None))
    bars=base.mark_bar(size=alt.ExprRef(expr="min(24, bandwidth('x') * 0.72)")).encode(y=alt.Y('일별 순매수(억원):Q',title='순매수 (억원)'),color=alt.condition(alt.datum['일별 순매수(억원)']>=0,alt.value('#ff5279'),alt.value('#287bff')),
          tooltip=['거래일',alt.Tooltip('일별 순매수(억원):Q',format=',.2f'),'수신/대상'])
    line=base.mark_line(color='#58a6ff',point=True,invalid='break-paths-show-domains').encode(y=alt.Y('누적 순매수(억원):Q',title='누적 (억원)'),tooltip=['거래일',alt.Tooltip('누적 순매수(억원):Q',format=',.2f')])
    view=bars if mode=='일별 순매수' else line if mode=='누적 순매수' else alt.layer(bars,line).resolve_scale(y='independent')
    view=view.properties(height=220,background='#091422').configure_view(stroke=None).configure_axis(labelColor='#b8cbe6',titleColor='#b8cbe6',gridColor='#21324a',gridOpacity=.5,domainColor='#2c4263',tickColor='#2c4263',labelFontSize=11,titleFontSize=11,labelPadding=8)
    st.altair_chart(view,width='stretch',key=key,theme=None)
    st.download_button('일별 수급 CSV',f[['trade_date','net_amount','cumulative_amount','observed_members','expected_members']].to_csv(index=False).encode('utf-8-sig'),key+'.csv','text/csv',key=key+'_csv',on_click='ignore')

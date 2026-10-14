"""Display helpers; chart gaps and downloads retain missing observations."""
import altair as alt
import streamlit as st


def chart(frame,period,key,title):
    f=frame.tail(period).copy()
    # Cumulative starts at the displayed window, and never resumes after an unknown observation.
    f['cumulative_amount']=f.net_amount.cumsum(skipna=False)
    f['거래일']=f.trade_date.dt.strftime('%Y-%m-%d')
    f['일별 순매수(억원)']=f.net_amount/1e8;f['누적 순매수(억원)']=f.cumulative_amount/1e8
    f['수신/대상']=f.observed_members.astype(str)+' / '+f.expected_members.astype(str)
    st.markdown('**'+title+'**')
    if f.net_amount.isna().any():st.caption('자료가 없는 날은 빈칸입니다. 합계와 누적선은 결측을 0으로 채우거나 건너뛰지 않습니다.')
    base=alt.Chart(f).encode(x=alt.X('거래일:O',sort=None,axis=alt.Axis(labelAngle=-45),title='거래일'))
    bars=base.mark_bar().encode(y=alt.Y('일별 순매수(억원):Q'),color=alt.condition(alt.datum['일별 순매수(억원)']>=0,alt.value('#128579'),alt.value('#bd6570')),
          tooltip=['거래일',alt.Tooltip('일별 순매수(억원):Q',format=',.2f'),'수신/대상'])
    line=base.mark_line(color='#3274b8',point=True,invalid='break-paths-show-domains').encode(y=alt.Y('누적 순매수(억원):Q'),tooltip=['거래일',alt.Tooltip('누적 순매수(억원):Q',format=',.2f')])
    view=alt.layer(bars,line).resolve_scale(y='independent').properties(height=240)
    st.altair_chart(view,width='stretch',key=key)
    st.caption('막대: 일별 순매수(왼쪽 축) · 파란선: 선택 기간 누적 순매수(오른쪽 축). 단위는 억원입니다.')
    with st.expander(title+' · 일별 원본 합계'):
        st.dataframe(f[['거래일','일별 순매수(억원)','누적 순매수(억원)','수신/대상']],hide_index=True)
        st.download_button('일별 수급 CSV',f[['trade_date','net_amount','cumulative_amount','observed_members','expected_members']].to_csv(index=False).encode('utf-8-sig'),key+'.csv','text/csv',key=key+'_csv')

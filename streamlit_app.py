import streamlit as st
from snowflake.snowpark.session import Session
import pandas as pd
import uuid

connection_parameters = {
    "user": st.secrets["SNOWFLAKE"]["USER"],
    "password": st.secrets["SNOWFLAKE"]["PASSWORD"],
    "account": st.secrets["SNOWFLAKE"]["ACCOUNT"],
    "warehouse": st.secrets["SNOWFLAKE"]["WAREHOUSE"],
    "database": st.secrets["SNOWFLAKE"]["DATABASE"],
    "schema": st.secrets["SNOWFLAKE"]["SCHEMA"],
    "role": st.secrets["SNOWFLAKE"]["ROLE"]
}

session = Session.builder.configs(connection_parameters).create()


st.set_page_config(page_title="🏠 AI Home Inspection", layout="wide")
st.title("🏠 AI-Assisted Home Inspection")

tab1, tab2 = st.tabs(["Inspector / Builder", "Regulator / NGO"])


with tab1:
    st.header("📝 Add New Inspection Finding")

    
    st.subheader("📁 Bulk Upload of Inspection Notes (CSV)")
    uploaded_file = st.file_uploader("Upload CSV with ROOM_ID and INSPECTOR_NOTES", type="csv")
    if uploaded_file:
        df_csv = pd.read_csv(uploaded_file)
        for _, row in df_csv.iterrows():
            room_exists = session.sql(f"SELECT COUNT(*) AS CNT FROM ROOMS WHERE ROOM_ID='{row['ROOM_ID']}'").collect()[0]['CNT']
            if room_exists == 0:
                st.warning(f"Room ID {row['ROOM_ID']} does not exist. Skipping row.")
                continue
            
            finding_id = str(uuid.uuid4())
            session.sql(f"""
                INSERT INTO INSPECTION_FINDINGS(FINDING_ID, ROOM_ID, INSPECTOR_NOTES)
                VALUES ('{finding_id}', '{row['ROOM_ID']}', '{row['INSPECTOR_NOTES']}')
            """).collect()
            
            session.sql(f"""
                INSERT INTO AI_TEXT_DEFECTS(FINDING_ID, ROOM_ID, DEFECT_TAG)
                SELECT
                    FINDING_ID,
                    ROOM_ID,
                    SNOWFLAKE.CORTEX.COMPLETE(
                        'mistral-large',
                        'Classify this inspection note into: exposed_wiring, crack, damp, finishing, ok. Note: ' || INSPECTOR_NOTES
                    ) AS DEFECT_TAG
                FROM INSPECTION_FINDINGS
                WHERE FINDING_ID = '{finding_id}'
            """).collect()
        st.success("✅ CSV processed successfully!")

    st.subheader("➕ Add Single Inspection Note")
    with st.form("new_finding_form"):
        room_id = st.text_input("Room ID (e.g., R1)")
        inspector_notes = st.text_area("Inspector Notes")
        submitted = st.form_submit_button("Add Finding")
        
        if submitted:
            finding_id = str(uuid.uuid4())
            session.sql(f"""
                INSERT INTO INSPECTION_FINDINGS(FINDING_ID, ROOM_ID, INSPECTOR_NOTES)
                VALUES ('{finding_id}', '{room_id}', '{inspector_notes}')
            """).collect()
            
            st.success("✅ New inspection finding added!")

            session.sql(f"""
                INSERT INTO AI_TEXT_DEFECTS(FINDING_ID, ROOM_ID, DEFECT_TAG)
                SELECT
                    FINDING_ID,
                    ROOM_ID,
                    SNOWFLAKE.CORTEX.COMPLETE(
                        'mistral-large',
                        'Classify this inspection note into: exposed_wiring, crack, damp, finishing, ok. Note: ' || INSPECTOR_NOTES
                    ) AS DEFECT_TAG
                FROM INSPECTION_FINDINGS
                WHERE FINDING_ID = '{finding_id}'
            """).collect()
            
            st.info("🤖 AI classification completed.")

    st.subheader("🏢 Current Property Risk")
    risk_df = session.sql("SELECT * FROM PROPERTY_RISK").to_pandas()

    def color_risk(level):
        if level == "HIGH": return "🟥 HIGH"
        if level == "MEDIUM": return "🟨 MEDIUM"
        return "🟩 LOW"

    if 'RISK_LEVEL' in risk_df.columns:
        risk_df['RISK_LEVEL_COLORED'] = risk_df['RISK_LEVEL'].apply(color_risk)
    st.dataframe(risk_df)


with tab2:
    st.header("📊 Property Risk Dashboard")

    risk_df = session.sql("SELECT * FROM PROPERTY_RISK").to_pandas()

    st.subheader("Filter Properties")
    risk_levels = st.multiselect("Select Risk Level", ["HIGH", "MEDIUM", "LOW"], default=["HIGH", "MEDIUM", "LOW"])
    search_property = st.text_input("Search Property by ID")

    filtered_df = risk_df
    if 'RISK_LEVEL' in filtered_df.columns:
        filtered_df = filtered_df[filtered_df['RISK_LEVEL'].isin(risk_levels)]
    if search_property:
        filtered_df = filtered_df[filtered_df['PROPERTY_ID'].str.contains(search_property)]
    st.dataframe(filtered_df)

    if 'RISK_LEVEL' in filtered_df.columns:
        high_risk_df = filtered_df[filtered_df['RISK_LEVEL'] == "HIGH"]
        st.subheader("🚨 High-Risk Properties")
        st.dataframe(high_risk_df)

    if not filtered_df.empty:
        selected_property = st.selectbox("Select Property to View Rooms", filtered_df['PROPERTY_ID'].tolist())
        room_df = session.sql(f"""
            SELECT R.ROOM_ID, R.ROOM_TYPE, D.DEFECT_TAG AS TEXT_DEFECT, I.IMAGE_LABEL AS IMAGE_DEFECT
            FROM ROOMS R
            LEFT JOIN AI_TEXT_DEFECTS D ON R.ROOM_ID = D.ROOM_ID
            LEFT JOIN IMAGE_FINDINGS I ON R.ROOM_ID = I.ROOM_ID
            WHERE R.PROPERTY_ID = '{selected_property}'
        """).to_pandas()
        st.subheader(f"Room-Level Defects: Property {selected_property}")
        st.dataframe(room_df)

        if 'ROOM_DEFECTS' in session.sql("SHOW TABLES").to_pandas()['name'].tolist():
            trend_df = session.sql(f"""
                SELECT PROPERTY_ID, INSPECTION_DATE,
                SUM(
                    CASE DEFECT_TAG
                        WHEN 'exposed_wiring' THEN 5
                        WHEN 'damp' THEN 4
                        WHEN 'crack' THEN 3
                        WHEN 'finishing' THEN 2
                        ELSE 0
                    END
                ) AS RISK_SCORE
                FROM ROOM_DEFECTS
                WHERE PROPERTY_ID = '{selected_property}'
                GROUP BY PROPERTY_ID, INSPECTION_DATE
                ORDER BY INSPECTION_DATE
            """).to_pandas()
            if not trend_df.empty:
                st.line_chart(trend_df.set_index('INSPECTION_DATE')['RISK_SCORE'])
            else:
                st.info("No trend data available yet for this property.")
        else:
            st.info("ROOM_DEFECTS table not found, skipping trend chart.")

        if 'ROOM_DEFECTS' in session.sql("SHOW TABLES").to_pandas()['name'].tolist():
            summary_df = session.sql(f"""
                SELECT
                    PROPERTY_ID,
                    SNOWFLAKE.CORTEX.COMPLETE(
                        'mistral-large',
                        'Write a simple home inspection summary: ' ||
                        LISTAGG(IMAGE_DEFECT, ', ')
                    ) AS SUMMARY
                FROM ROOM_DEFECTS
                WHERE PROPERTY_ID = '{selected_property}'
                GROUP BY PROPERTY_ID
            """).to_pandas()
            for _, row in summary_df.iterrows():
                st.subheader("📝 AI Inspection Summary")
                st.markdown(f"**Property {row['PROPERTY_ID']}**")
                st.write(row['SUMMARY'])

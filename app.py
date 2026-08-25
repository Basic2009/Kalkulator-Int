import streamlit as st
import pandas as pd
import io
import requests
from pulp import LpProblem, LpMinimize, LpVariable, lpSum, LpStatus, PULP_CBC_CMD

st.set_page_config(page_title="Kalkulator Blendowania TW", layout="wide")

st.title("🍹 System Optymalizacji Kupaży Koncentratów (TW-1 .. TW-40)")
st.markdown("---")

# Sidebar - Parametry wejściowe
st.sidebar.header("⚙️ Parametry Docelowe Blend do uzyskania")

gdrive_id = st.sidebar.text_input("ID pliku Google Drive", value="1t6ssjST2jEFoFRvM5OIMgebzV7HMYwJv")
docelowa_ilosc = st.sidebar.number_input("Docelowa ilość [KG]", value=10000.0, step=500.0)
docelowy_brix = st.sidebar.number_input("Sztywny Brix [°Bx]", value=65.0, step=0.1)

st.sidebar.subheader("Kwasowość")
# Poprawiony parametr: horizontal=True
kwas_jednostka = st.sidebar.radio("Jednostka Kwasowości", ["CA", "MA"], horizontal=True)
col_k1, col_k2 = st.sidebar.columns(2)
kwas_min = col_k1.number_input("Kwas MIN", value=2.1, step=0.05)
kwas_max = col_k2.number_input("Kwas MAX", value=2.3, step=0.05)

st.sidebar.subheader("Barwa")
# Poprawiony parametr: horizontal=True
barwa_jednostka = st.sidebar.radio("Jednostka Barwy", ["T", "A"], horizontal=True)
col_b1, col_b2 = st.sidebar.columns(2)
barwa_min = col_b1.number_input("Barwa MIN", value=40.0 if barwa_jednostka == "T" else 0.20, step=1.0 if barwa_jednostka == "T" else 0.05)
barwa_max = col_b2.number_input("Barwa MAX", value=50.0 if barwa_jednostka == "T" else 0.40, step=1.0 if barwa_jednostka == "T" else 0.05)

pozwol_na_wode = st.sidebar.checkbox("Dolewaj wodę gdy Brix za wysoki", value=True)

# Pobieranie danych
@st.cache_data(ttl=5)
def pobierz_dane(file_id):
    url = f'https://drive.google.com/uc?export=download&id={file_id}'
    response = requests.get(url)
    response.raise_for_status()
    return pd.read_excel(io.BytesIO(response.content), engine='openpyxl')

if st.sidebar.button("🚀 OBLICZ RECEPTURY", type="primary"):
    try:
        df = pobierz_dane(gdrive_id)
        df.columns = df.columns.str.strip()
        
        KOLUMNA_KWAS = f'Kwasowość ({kwas_jednostka})'
        KOLUMNA_BARWA = f'Barwa ({barwa_jednostka})'
        
        kolumny_numeryczne = ['Ilość (KG)', 'Zablokowana Ilość', 'Brix', 'Kwasowość (MA)', 'Kwasowość (CA)', 'Barwa (T)', 'Barwa (A)']
        for col in kolumny_numeryczne:
            if col in df.columns:
                df[col] = df[col].astype(str).str.replace(',', '.').str.strip()
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        if 'Zablokowana Ilość' not in df.columns:
            df['Zablokowana Ilość'] = 0.0

        df['Dostępne_Netto'] = (df['Ilość (KG)'] - df['Zablokowana Ilość']).clip(lower=0)
        df = df.dropna(subset=['Zbiornik', KOLUMNA_KWAS, KOLUMNA_BARWA, 'Brix'])
        df = df[df['Dostępne_Netto'] > 0]

        if 'Barwa (T)' in df.columns and df['Barwa (T)'].max() <= 1.0:
            df['Barwa (T)'] = df['Barwa (T)'] * 100

        if pozwol_na_wode:
            woda_df = pd.DataFrame([{
                'Zbiornik': 'WODA (Dodatek)',
                'Ilość (KG)': 999999.0,
                'Zablokowana Ilość': 0.0,
                'Dostępne_Netto': 999999.0,
                'Brix': 0.0,
                'Kwasowość (MA)': 0.0,
                'Kwasowość (CA)': 0.0,
                'Barwa (T)': 100.0,
                'Barwa (A)': 0.0
            }])
            df = pd.concat([df, woda_df], ignore_index=True)

        def licz_blend(podejscie):
            prob = LpProblem(f"Blend_{podejscie}", LpMinimize)
            v_vars = {}
            y_vars = {}
            for idx, row in df.iterrows():
                t_id = str(row['Zbiornik']).strip()
                max_kg = float(row['Dostępne_Netto'])
                v_vars[t_id] = LpVariable(f"v_{idx}", 0, max_kg)
                y_vars[t_id] = LpVariable(f"y_{idx}", cat='Binary')
                prob += v_vars[t_id] <= max_kg * y_vars[t_id]

            prob += lpSum([v_vars[t] for t in v_vars]) == docelowa_ilosc
            prob += lpSum([v_vars[str(row['Zbiornik']).strip()] * float(row['Brix']) for _, row in df.iterrows()]) == docelowa_ilosc * docelowy_brix
            prob += lpSum([v_vars[str(row['Zbiornik']).strip()] * float(row[KOLUMNA_KWAS]) for _, row in df.iterrows()]) >= docelowa_ilosc * kwas_min
            prob += lpSum([v_vars[str(row['Zbiornik']).strip()] * float(row[KOLUMNA_KWAS]) for _, row in df.iterrows()]) <= docelowa_ilosc * kwas_max
            prob += lpSum([v_vars[str(row['Zbiornik']).strip()] * float(row[KOLUMNA_BARWA]) for _, row in df.iterrows()]) >= docelowa_ilosc * barwa_min
            prob += lpSum([v_vars[str(row['Zbiornik']).strip()] * float(row[KOLUMNA_BARWA]) for _, row in df.iterrows()]) <= docelowa_ilosc * barwa_max

            suma_kwasu = lpSum([v_vars[str(row['Zbiornik']).strip()] * float(row[KOLUMNA_KWAS]) for _, row in df.iterrows()])
            suma_barwy = lpSum([v_vars[str(row['Zbiornik']).strip()] * float(row[KOLUMNA_BARWA]) for _, row in df.iterrows()])
            uzyte_tanki = lpSum([y_vars[t] for t in y_vars if t != 'WODA (Dodatek)'])

            if podejscie == 'min_kwas':
                prob += suma_kwasu * 1000 + uzyte_tanki * 10
            elif podejscie == 'min_barwa':
                prob += suma_barwy * 1000 + uzyte_tanki * 10
            elif podejscie == 'min_oba':
                prob += suma_kwasu * 1000 + suma_barwy * 1000 + uzyte_tanki * 10

            prob.solve(PULP_CBC_CMD(msg=0))
            if LpStatus[prob.status] != 'Optimal':
                return None

            wyniki = []
            tot_mass = 0
            tot_brix = 0
            tot_kwas_ma = 0
            tot_kwas_ca = 0
            tot_barwa_t = 0
            tot_barwa_a = 0
            
            for _, row in df.iterrows():
                t = str(row['Zbiornik']).strip()
                val = v_vars[t].varValue
                if val and val > 0.1:
                    wyniki.append({
                        'Zbiornik': t, 
                        'Pobrano [KG]': round(val, 1), 
                        'Dostępne Netto [KG]': float(row['Dostępne_Netto']),
                        'Zablokowane [KG]': float(row['Zablokowana Ilość'])
                    })
                    tot_mass += val
                    tot_brix += val * float(row['Brix'])
                    if 'Kwasowość (MA)' in df.columns: tot_kwas_ma += val * float(row['Kwasowość (MA)'])
                    if 'Kwasowość (CA)' in df.columns: tot_kwas_ca += val * float(row['Kwasowość (CA)'])
                    if 'Barwa (T)' in df.columns: tot_barwa_t += val * float(row['Barwa (T)'])
                    if 'Barwa (A)' in df.columns: tot_barwa_a += val * float(row['Barwa (A)'])
                    
            return {
                'sklad': pd.DataFrame(wyniki),
                'brix': round(tot_brix / tot_mass, 2),
                'kwas_ma': round(tot_kwas_ma / tot_mass, 2) if 'Kwasowość (MA)' in df.columns else None,
                'kwas_ca': round(tot_kwas_ca / tot_mass, 2) if 'Kwasowość (CA)' in df.columns else None,
                'barwa_t': round(tot_barwa_t / tot_mass, 2) if 'Barwa (T)' in df.columns else None,
                'barwa_a': round(tot_barwa_a / tot_mass, 3) if 'Barwa (A)' in df.columns else None,
                'uzyte_tanki': len([x for x in wyniki if x['Zbiornik'] != 'WODA (Dodatek)'])
            }

        tab1, tab2, tab3 = st.tabs(["📉 1. Najniższa Kwasowość", "🎨 2. Najniższa Barwa", "⚖️ 3. Hybryda (Min Kwas + Barwa)"])

        warianty_map = {
            tab1: ("min_kwas", "Wariant z najniższą możliwą kwasowością"),
            tab2: ("min_barwa", "Wariant z najniższym/najjaśniejszym kolorem"),
            tab3: ("min_oba", "Wariant ze zrównoważonym najniższym kwasem i barwą")
        }

        for tab, (kod, opisy) in warianty_map.items():
            with tab:
                res = licz_blend(kod)
                if res:
                    st.success(f"{opisy} | Użyte tanki: {res['uzyte_tanki']}")
                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("Łączna masa", f"{docelowa_ilosc:.0f} KG")
                    col2.metric("Wynikowy Brix", f"{res['brix']} °Bx")
                    
                    kwas_val = f"{res['kwas_ca']} CA" if kwas_jednostka == 'CA' else f"{res['kwas_ma']} MA"
                    col3.metric("Wynikowa Kwasowość", kwas_val)
                    
                    barwa_val = f"{res['barwa_t']}% T" if barwa_jednostka == 'T' else f"{res['barwa_a']} A"
                    col4.metric("Wynikowa Barwa", barwa_val)
                    
                    st.dataframe(res['sklad'], use_container_width=True)
                else:
                    st.error("Brak możliwości ułożenia blendu w podanych zakresach.")

    except Exception as e:
        st.error(f"Błąd przetwarzania: {e}")

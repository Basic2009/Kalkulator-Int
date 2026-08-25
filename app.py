import io
import pandas as pd
from pulp import PULP_CBC_CMD, LpInteger, LpMinimize, LpProblem, LpStatus, lpSum, LpVariable
import requests
import streamlit as st

st.set_page_config(page_title="Kalkulator Blendów", layout="wide")

st.title("🍎 Interaktywny Kalkulator Blendów 🍒")
st.markdown("---")

gdrive_id = "1t6ssjST2jEFoFRvM5OIMgebzV7HMYwJv"

# Krok inkrementu dla zbiorników (kg)
STEP_KG = 100.0


@st.cache_data(ttl=60)
def pobierz_dane(file_id):
    url = f"https://drive.google.com/uc?export=download&id={file_id}"
    response = requests.get(url)
    response.raise_for_status()
    return pd.read_excel(io.BytesIO(response.content), engine="openpyxl")


try:
    df_raw = pobierz_dane(gdrive_id)
    df_raw.columns = df_raw.columns.str.strip()
    lista_zbiornikow = (
        df_raw["Zbiornik"].dropna().astype(str).str.strip().unique().tolist()
    )
except Exception:
    lista_zbiornikow = []

st.sidebar.header("⚙️ Parametry Docelowe")

domyslne_zaznaczenie = [z.startswith("TZH-") for z in lista_zbiornikow]

df_selekcja_init = pd.DataFrame({
    "Zbiornik": lista_zbiornikow,
    "Dostępny": domyslne_zaznaczenie
})

with st.sidebar.expander("📋 Wybór dostępnych zbiorników", expanded=False):
    df_selekcja = st.data_editor(
        df_selekcja_init,
        column_config={
            "Dostępny": st.column_config.CheckboxColumn("Użyj", default=False),
            "Zbiornik": st.column_config.TextColumn("Zbiornik", disabled=True),
        },
        disabled=["Zbiornik"],
        hide_index=True,
    )

wybrane_zbiorniki = df_selekcja[df_selekcja["Dostępny"] == True]["Zbiornik"].tolist()

docelowa_ilosc_koncentratu = st.sidebar.number_input(
    "Docelowa ilość [KG]", value=100000.0, step=STEP_KG
)
docelowy_brix = st.sidebar.number_input(
    "Min. Brix [°Bx]", value=70.0, step=0.1
)

max_uzytych_tankow = st.sidebar.number_input(
    "Maksymalna liczba zbiorników", value=3, min_value=1, max_value=40, step=1
)

st.sidebar.subheader("Kwasowość")
kwas_jednostka = st.sidebar.radio(
    "Jednostka Kwasowości", ["CA", "MA"], horizontal=True
)
col_k1, col_k2 = st.sidebar.columns(2)
kwas_min = col_k1.number_input("Kwas MIN", value=2.2, step=0.01)
kwas_max = col_k2.number_input("Kwas MAX", value=2.4, step=0.01)

st.sidebar.subheader("Barwa")
barwa_jednostka = st.sidebar.radio(
    "Jednostka Barwy", ["Trans", "Abs"], horizontal=True
)
col_b1, col_b2 = st.sidebar.columns(2)
barwa_min = col_b1.number_input(
    "Barwa MIN",
    value=40.0 if barwa_jednostka == "Trans" else 0.40,
    step=0.5 if barwa_jednostka == "Trans" else 0.01,
)
barwa_max = col_b2.number_input(
    "Barwa MAX",
    value=50.0 if barwa_jednostka == "Trans" else 0.50,
    step=0.5 if barwa_jednostka == "Trans" else 0.01,
)

pozwol_na_wode = st.sidebar.checkbox(
    "Zbijanie Brixa wodą", value=True
)

if st.sidebar.button("🚀 Losu Losu", type="primary"):
    st.session_state["obliczono"] = True
    for key in list(st.session_state.keys()):
        if key.startswith("df_res_"):
            del st.session_state[key]

if st.session_state.get("obliczono", False):
    try:
        df = pobierz_dane(gdrive_id)
        df.columns = df.columns.str.strip()

        KOLUMNA_KWAS = f"Kwasowość ({kwas_jednostka})"
        KOLUMNA_BARWA = f"Barwa ({barwa_jednostka})"

        kolumny_numeryczne = [
            "Ilość (KG)",
            "Zablokowana Ilość",
            "Brix",
            "Kwasowość (MA)",
            "Kwasowość (CA)",
            "Barwa (Trans)",
            "Barwa (Abs)",
        ]
        for col in kolumny_numeryczne:
            if col in df.columns:
                df[col] = df[col].astype(str).str.replace(",", ".").str.strip()
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

        if "Zablokowana Ilość" not in df.columns:
            df["Zablokowana Ilość"] = 0.0

        df["Zbiornik"] = df["Zbiornik"].astype(str).str.strip()
        df["Dostępne_Netto"] = (
            df["Ilość (KG)"] - df["Zablokowana Ilość"]
        ).clip(lower=0)
        df = df.dropna(subset=["Zbiornik", KOLUMNA_KWAS, KOLUMNA_BARWA, "Brix"])
        
        if "Barwa (Trans)" in df.columns and df["Barwa (Trans)"].max() <= 1.0:
            df["Barwa (Trans)"] = df["Barwa (Trans)"] * 100

        df_calc = df[(df["Dostępne_Netto"] > 0) & (df["Zbiornik"].isin(wybrane_zbiorniki))].copy()

        if pozwol_na_wode:
            woda_df = pd.DataFrame([{
                "Zbiornik": "WODA (Dodatek)",
                "Ilość (KG)": 999999.0,
                "Zablokowana Ilość": 0.0,
                "Dostępne_Netto": 999999.0,
                "Brix": 0.0,
                "Kwasowość (MA)": 0.0,
                "Kwasowość (CA)": 0.0,
                "Barwa (Trans)": 100.0,
                "Barwa (Abs)": 0.0,
            }])
            df_calc = pd.concat([df_calc, woda_df], ignore_index=True)

        def licz_blend(podejscie):
            prob = LpProblem(f"Blend_{podejscie}", LpMinimize)
            v_vars, y_vars, n_vars = {}, {}, {}

            for idx, row in df_calc.iterrows():
                t_id = str(row["Zbiornik"]).strip()
                max_kg = float(row["Dostępne_Netto"])

                y_vars[t_id] = LpVariable(f"y_{idx}", cat="Binary")

                if t_id == "WODA (Dodatek)":
                    v_vars[t_id] = LpVariable(f"v_woda_{idx}", 0, max_kg)
                    prob += v_vars[t_id] <= max_kg * y_vars[t_id]
                else:
                    max_steps = int(max_kg // STEP_KG)
                    n_vars[t_id] = LpVariable(f"n_{idx}", 0, max_steps, cat=LpInteger)
                    v_vars[t_id] = n_vars[t_id] * STEP_KG
                    prob += n_vars[t_id] <= max_steps * y_vars[t_id]

            prob += (
                lpSum([v_vars[t] for t in v_vars if t != "WODA (Dodatek)"])
                == docelowa_ilosc_koncentratu
            )

            prob += (
                lpSum([y_vars[t] for t in y_vars if t != "WODA (Dodatek)"])
                <= max_uzytych_tankow
            )

            masa_calkowita = lpSum([v_vars[t] for t in v_vars])

            if pozwol_na_wode:
                prob += (
                    lpSum([
                        v_vars[str(row["Zbiornik"]).strip()] * float(row["Brix"])
                        for _, row in df_calc.iterrows()
                    ])
                    == masa_calkowita * docelowy_brix
                )
            else:
                prob += (
                    lpSum([
                        v_vars[str(row["Zbiornik"]).strip()] * float(row["Brix"])
                        for _, row in df_calc.iterrows()
                    ])
                    >= docelowa_ilosc_koncentratu * docelowy_brix
                )

            prob += (
                lpSum([
                    v_vars[str(row["Zbiornik"]).strip()] * float(row[KOLUMNA_KWAS])
                    for _, row in df_calc.iterrows()
                ])
                >= masa_calkowita * kwas_min
            )
            prob += (
                lpSum([
                    v_vars[str(row["Zbiornik"]).strip()] * float(row[KOLUMNA_KWAS])
                    for _, row in df_calc.iterrows()
                ])
                <= masa_calkowita * kwas_max
            )

            prob += (
                lpSum([
                    v_vars[str(row["Zbiornik"]).strip()] * float(row[KOLUMNA_BARWA])
                    for _, row in df_calc.iterrows()
                ])
                >= masa_calkowita * barwa_min
            )
            prob += (
                lpSum([
                    v_vars[str(row["Zbiornik"]).strip()] * float(row[KOLUMNA_BARWA])
                    for _, row in df_calc.iterrows()
                ])
                <= masa_calkowita * barwa_max
            )

            suma_kwasu = lpSum([
                v_vars[str(row["Zbiornik"]).strip()] * float(row[KOLUMNA_KWAS])
                for _, row in df_calc.iterrows()
            ])
            suma_barwy = lpSum([
                v_vars[str(row["Zbiornik"]).strip()] * float(row[KOLUMNA_BARWA])
                for _, row in df_calc.iterrows()
            ])

            kwas_ref = (kwas_min + kwas_max) / 2.0 if kwas_max > 0 else 1.0
            barwa_ref = (barwa_min + barwa_max) / 2.0 if barwa_max > 0 else 1.0

            norm_kwas = suma_kwasu / kwas_ref

            if barwa_jednostka == "Trans":
                norm_barwa = suma_barwy / barwa_ref
            else:
                norm_barwa = (docelowa_ilosc_koncentratu * barwa_max - suma_barwy) / barwa_ref

            uzyte_tanki = lpSum([y_vars[t] for t in y_vars if t != "WODA (Dodatek)"])

            if podejscie == "min_kwas":
                prob += norm_kwas * 1000 + uzyte_tanki * 10
            elif podejscie == "min_barwa":
                prob += norm_barwa * 1000 + uzyte_tanki * 10
            elif podejscie == "min_oba":
                prob += norm_kwas * 800 + norm_barwa * 100 + uzyte_tanki * 10

            prob.solve(PULP_CBC_CMD(msg=0))
            if LpStatus[prob.status] != "Optimal":
                return None

            wyniki = []
            for _, row in df_calc.iterrows():
                t = str(row["Zbiornik"]).strip()
                is_woda = (t == "WODA (Dodatek)")

                if is_woda:
                    val = v_vars[t].varValue
                else:
                    val = n_vars[t].varValue * STEP_KG if n_vars[t].varValue is not None else 0.0

                if val and val > 0.001:
                    pobrano = round(val, 1) if is_woda else float(round(val))
                    stan_aktualny = float(row["Ilość (KG)"])
                    zablokowane = float(row["Zablokowana Ilość"])
                    dostepne = float(row["Dostępne_Netto"])

                    wyniki.append({
                        "Zbiornik": t,
                        "Pobrano [KG]": pobrano,
                        "Stan Aktualny [KG]": "—" if is_woda else stan_aktualny,
                        "Zablokowane [KG]": "—" if is_woda else zablokowane,
                        "Dostępne [KG]": 999999.0 if is_woda else dostepne,
                        "Pozostanie [KG]": "—" if is_woda else round(dostepne - pobrano, 1),
                        "Brix [°Bx]": float(row["Brix"]),
                        "Kwas MA": float(row["Kwasowość (MA)"]) if "Kwasowość (MA)" in df.columns else 0.0,
                        "Kwas CA": float(row["Kwasowość (CA)"]) if "Kwasowość (CA)" in df.columns else 0.0,
                        "Barwa Trans [%]": float(row["Barwa (Trans)"]) if "Barwa (Trans)" in df.columns else 0.0,
                        "Barwa Abs": float(row["Barwa (Abs)"]) if "Barwa (Abs)" in df.columns else 0.0,
                    })

            return pd.DataFrame(wyniki)

        tab1, tab2, tab3 = st.tabs([
            "📉 1. Najniższa Kwasowość",
            "🎨 2. Najniższa Barwa",
            "⚖️ 3. Najniższa Kwasowość + Barwa",
        ])

        warianty_map = {
            tab1: ("min_kwas", "Wariant z najniższą możliwą kwasowością"),
            tab2: ("min_barwa", "Wariant z najniższym/najjaśniejszym kolorem"),
            tab3: ("min_oba", "Wariant ze zrównoważonym najniższym kwasem i barwą"),
        }

        def przelicz_kupaż(df_edited):
            tot_mass = df_edited["Pobrano [KG]"].sum()
            if tot_mass <= 0:
                return None

            tot_brix = (df_edited["Pobrano [KG]"] * df_edited["Brix [°Bx]"]).sum() / tot_mass
            tot_kwas_ma = (df_edited["Pobrano [KG]"] * df_edited["Kwas MA"]).sum() / tot_mass
            tot_kwas_ca = (df_edited["Pobrano [KG]"] * df_edited["Kwas CA"]).sum() / tot_mass
            tot_barwa_trans = (df_edited["Pobrano [KG]"] * df_edited["Barwa Trans [%]"]).sum() / tot_mass
            tot_barwa_abs = (df_edited["Pobrano [KG]"] * df_edited["Barwa Abs"]).sum() / tot_mass
            
            uzyte_tanki = len(df_edited[(df_edited["Zbiornik"] != "WODA (Dodatek)") & (df_edited["Pobrano [KG]"] > 0)])

            df_edited["Pozostanie [KG]"] = df_edited.apply(
                lambda r: "—" if r["Zbiornik"] == "WODA (Dodatek)" else round(r["Dostępne [KG]"] - r["Pobrano [KG]"], 1),
                axis=1
            )

            return {
                "tot_mass": round(tot_mass, 1),
                "brix": round(tot_brix, 2),
                "kwas_ma": round(tot_kwas_ma, 2),
                "kwas_ca": round(tot_kwas_ca, 2),
                "barwa_trans": round(tot_barwa_trans, 2),
                "barwa_abs": round(tot_barwa_abs, 3),
                "uzyte_tanki": uzyte_tanki
            }

        for tab, (kod, opisy) in warianty_map.items():
            with tab:
                key_df = f"df_res_{kod}"
                if key_df not in st.session_state:
                    res_df = licz_blend(kod)
                    st.session_state[key_df] = res_df
                else:
                    res_df = st.session_state[key_df]

                if res_df is not None and not res_df.empty:
                    # 1. NA GÓRZE: Statystyki i opis wariantu
                    stats = przelicz_kupaż(res_df)

                    if stats:
                        st.success(f"{opisy} | Użyte tanki: {stats['uzyte_tanki']}")
                        col1, col2, col3, col4 = st.columns(4)
                        
                        col1.metric("Masa Całkowita (z wodą)", f"{stats['tot_mass']:.1f} KG")
                        
                        brix_ok = stats['brix'] >= docelowy_brix
                        kwas_val = stats['kwas_ca'] if kwas_jednostka == 'CA' else stats['kwas_ma']
                        kwas_ok = kwas_min <= kwas_val <= kwas_max
                        
                        barwa_val = stats['barwa_trans'] if barwa_jednostka == 'Trans' else stats['barwa_abs']
                        barwa_ok = barwa_min <= barwa_val <= barwa_max

                        col2.metric("Wynikowy Brix", f"{stats['brix']} °Bx")
                        if not brix_ok:
                            st.caption("⚠️ Brix poniżej minimum!")

                        col3.metric("Wynikowa Kwasowość", f"{kwas_val} {kwas_jednostka}")
                        if not kwas_ok:
                            st.caption("⚠️ Kwasowość poza zakresem!")

                        col4.metric("Wynikowa Barwa", f"{barwa_val} {barwa_jednostka}")
                        if not barwa_ok:
                            st.caption("⚠️ Barwa poza zakresem!")

                        st.markdown("---")
                    
                    # 2. NA ŚRODKU: Tabela blendu
                    edited_df = st.data_editor(
                        res_df,
                        column_config={
                            "Pobrano [KG]": st.column_config.NumberColumn(
                                "Pobrano [KG]",
                                min_value=0.0,
                                step=100.0,
                            ),
                            "Zbiornik": st.column_config.TextColumn("Zbiornik", disabled=True),
                            "Stan Aktualny [KG]": st.column_config.TextColumn("Stan Aktualny [KG]", disabled=True),
                            "Zablokowane [KG]": st.column_config.TextColumn("Zablokowane [KG]", disabled=True),
                            "Dostępne [KG]": st.column_config.NumberColumn("Dostępne [KG]", disabled=True, format="%.1f"),
                            "Pozostanie [KG]": st.column_config.TextColumn("Pozostanie [KG]", disabled=True),
                            "Brix [°Bx]": st.column_config.NumberColumn("Brix [°Bx]", disabled=True),
                            "Kwas MA": st.column_config.NumberColumn("Kwas MA", disabled=True),
                            "Kwas CA": st.column_config.NumberColumn("Kwas CA", disabled=True),
                            "Barwa Trans [%]": st.column_config.NumberColumn("Barwa Trans [%]", disabled=True),
                            "Barwa Abs": st.column_config.NumberColumn("Barwa Abs", disabled=True),
                        },
                        hide_index=True,
                        use_container_width=True,
                        key=f"editor_{kod}"
                    )

                    st.session_state[key_df] = edited_df

                    # 3. POD TABELĄ: Informacja o edycji
                    st.info("💡 Możesz edytować kolumnę **Pobrano [KG]** w tabeli powyżej lub ręcznie **dodać kolejny zbiornik** poniżej.")

                    # 4. NA SAMYM DOLE: Sekcja dodawania zbiornika
                    uzyte_nazwy = edited_df["Zbiornik"].tolist()
                    niewykorzystane_df = df[(~df["Zbiornik"].isin(uzyte_nazwy)) & (df["Dostępne_Netto"] > 0)]
                    opcje_zbiornikow = niewykorzystane_df["Zbiornik"].tolist()

                    if opcje_zbiornikow:
                        with st.expander("➕ Dodaj kolejny zbiornik do blenda"):
                            col_add1, col_add2, col_add3 = st.columns([2, 2, 1])
                            wybrany_nowy_tank = col_add1.selectbox(
                                "Wybierz zbiornik do dodania", opcje_zbiornikow, key=f"sel_add_{kod}"
                            )
                            dostepne_kg_tank = niewykorzystane_df[niewykorzystane_df["Zbiornik"] == wybrany_nowy_tank]["Dostępne_Netto"].values[0]
                            
                            pobrana_ilosc_nowy = col_add2.number_input(
                                f"Ilość do pobrania [KG] (max: {dostepne_kg_tank:.0f})",
                                min_value=100.0,
                                max_value=float(dostepne_kg_tank),
                                step=100.0,
                                value=1000.0,
                                key=f"num_add_{kod}"
                            )
                            
                            if col_add3.button("Dodaj do tabeli", key=f"btn_add_{kod}"):
                                row_data = niewykorzystane_df[niewykorzystane_df["Zbiornik"] == wybrany_nowy_tank].iloc[0]
                                new_row = pd.DataFrame([{
                                    "Zbiornik": wybrany_nowy_tank,
                                    "Pobrano [KG]": pobrana_ilosc_nowy,
                                    "Stan Aktualny [KG]": float(row_data["Ilość (KG)"]),
                                    "Zablokowane [KG]": float(row_data["Zablokowana Ilość"]),
                                    "Dostępne [KG]": float(row_data["Dostępne_Netto"]),
                                    "Pozostanie [KG]": round(float(row_data["Dostępne_Netto"]) - pobrana_ilosc_nowy, 1),
                                    "Brix [°Bx]": float(row_data["Brix"]),
                                    "Kwas MA": float(row_data["Kwasowość (MA)"]) if "Kwasowość (MA)" in df.columns else 0.0,
                                    "Kwas CA": float(row_data["Kwasowość (CA)"]) if "Kwasowość (CA)" in df.columns else 0.0,
                                    "Barwa Trans [%]": float(row_data["Barwa (Trans)"]) if "Barwa (Trans)" in df.columns else 0.0,
                                    "Barwa Abs": float(row_data["Barwa (Abs)"]) if "Barwa (Abs)" in df.columns else 0.0,
                                }])
                                
                                st.session_state[key_df] = pd.concat([edited_df, new_row], ignore_index=True)
                                st.rerun()

                    przekroczenia = edited_df[edited_df["Pobrano [KG]"] > edited_df["Dostępne [KG]"]]
                    if not przekroczenia.empty:
                        st.error(f"🚨 Przekroczono dostępne stany w zbiornikach: {', '.join(przekroczenia['Zbiornik'].tolist())}!")

                else:
                    st.error("Brak możliwości ułożenia blendu w podanych zakresach. Sprawdź zaznaczone zbiorniki lub poszerz parametry.")

    except Exception as e:
        st.error(f"Błąd przetwarzania: {e}")

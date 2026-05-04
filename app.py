import streamlit as st
import pdfplumber
import pytesseract
from pdf2image import convert_from_bytes
import pandas as pd
import re
from io import BytesIO

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

st.title("Comparador de nòmines")
st.write("Mostra només treballadors amb incidències respecte el mes anterior.")

pdf_mes_anterior = st.file_uploader("PDF mes anterior", type="pdf")
pdf_mes_actual = st.file_uploader("PDF mes actual", type="pdf")


def convertir_numero(valor):
    try:
        return float(valor.replace(".", "").replace(",", "."))
    except:
        return 0.0


def format_euro(valor):
    try:
        return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return ""


def format_comparacio(anterior, actual):
    diferencia = actual - anterior
    signe = "+" if diferencia > 0 else ""
    return f"{format_euro(anterior)} → {format_euro(actual)} ({signe}{format_euro(diferencia)})"


def llegir_pdf(pdf):
    text_total = ""

    try:
        with pdfplumber.open(pdf) as document:
            for pagina in document.pages:
                text = pagina.extract_text()
                if text:
                    text_total += text + "\n"
    except:
        pass

    if text_total.strip() == "":
        pdf.seek(0)
        images = convert_from_bytes(pdf.read())
        for img in images:
            text_total += pytesseract.image_to_string(img, lang="spa") + "\n"

    return text_total


def extreure_blocs(text):
    blocs = re.split(r"(?=Empresa\s+\d+)", text)
    return [b for b in blocs if b.strip().startswith("Empresa")]


def extreure_empresa(bloc):
    match = re.search(r"Empresa\s+(\d+)\s+([A-Z0-9]+)\s+(.+)", bloc)
    if not match:
        return "", ""
    return match.group(1).strip(), match.group(3).strip()


def linia_que_comenca(bloc, inici):
    for linia in bloc.splitlines():
        if re.match(inici, linia.strip(), re.IGNORECASE):
            return linia.strip()
    return ""


def netejar_tokens(tokens):
    eliminar = {"TOTAL", "EMPRESA", "SUMA", "Y", "SIGUE"}
    return [t for t in tokens if t.upper() not in eliminar]


def extreure_empleats(bloc):
    linia_codis = linia_que_comenca(bloc, r"^Empleado")
    linia_nom = linia_que_comenca(bloc, r"^Nombre|^Nom")
    linia_cognom1 = linia_que_comenca(bloc, r"^Primer")
    linia_cognom2 = linia_que_comenca(bloc, r"^Segundo")

    codis = netejar_tokens(linia_codis.replace("Empleado", "").split())
    noms = netejar_tokens(re.sub(r"^Nombre|^Nom", "", linia_nom, flags=re.IGNORECASE).split())
    cognoms1 = netejar_tokens(re.sub(r"^Primer\s+\S+", "", linia_cognom1, flags=re.IGNORECASE).split())
    cognoms2 = netejar_tokens(re.sub(r"^Segundo\s+\S+", "", linia_cognom2, flags=re.IGNORECASE).split())

    empleats = []

    for i, codi in enumerate(codis):
        nom = noms[i] if i < len(noms) else ""
        c1 = cognoms1[i] if i < len(cognoms1) else ""
        c2 = cognoms2[i] if i < len(cognoms2) else ""

        empleats.append({
            "posicio": i,
            "codi": codi,
            "treballador": f"{nom} {c1} {c2}".strip()
        })

    return empleats


def extreure_valors_de_linia(bloc, etiqueta):
    for linia in bloc.splitlines():
        if re.match(etiqueta, linia.strip(), re.IGNORECASE):
            valors = re.findall(r"-?\d{1,3}(?:\.\d{3})*,\d{2}", linia)
            return [convertir_numero(v) for v in valors]
    return []


def extreure_total_correcte(bloc):
    for linia in bloc.splitlines():
        linia_neta = linia.strip().upper()

        if (
            linia_neta.startswith("TOTAL ")
            and not linia_neta.startswith("TOTAL DEVENGOS")
            and not linia_neta.startswith("TOTAL RETEN")
            and not linia_neta.startswith("TOTAL L")
            and not linia_neta.startswith("TOTAL COSTE")
        ):
            return [convertir_numero(v) for v in re.findall(r"-?\d{1,3}(?:\.\d{3})*,\d{2}", linia)]

    return []


def processar_pdf(text):
    registres = []

    for bloc in extreure_blocs(text):
        codi_empresa, nom_empresa = extreure_empresa(bloc)
        empleats = extreure_empleats(bloc)

        devengos = extreure_valors_de_linia(bloc, r"^TOTAL DEVENGOS")
        retencion = extreure_valors_de_linia(bloc, r"^TOTAL RETENCI[ÓO]N")
        liquido = extreure_valors_de_linia(bloc, r"^TOTAL L[ÍI]QUIDO")
        total = extreure_total_correcte(bloc)

        for emp in empleats:
            i = emp["posicio"]

            registres.append({
                "Empresa": codi_empresa,
                "Nom empresa": nom_empresa,
                "Codi treballador": emp["codi"],
                "Treballador": emp["treballador"],
                "TOTAL DEVENGOS": devengos[i] if i < len(devengos) else 0,
                "TOTAL RETENCION": retencion[i] if i < len(retencion) else 0,
                "TOTAL LIQUIDO": liquido[i] if i < len(liquido) else 0,
                "TOTAL": total[i] if i < len(total) else 0,
            })

    df = pd.DataFrame(registres)

    if df.empty:
        return df

    df = df.groupby(
        ["Empresa", "Nom empresa", "Codi treballador"],
        as_index=False
    ).agg({
        "Treballador": "last",
        "TOTAL DEVENGOS": "sum",
        "TOTAL RETENCION": "sum",
        "TOTAL LIQUIDO": "sum",
        "TOTAL": "sum"
    })

    return df


def comparar(df_ant, df_act):
    claus = ["Empresa", "Nom empresa", "Codi treballador"]

    df = pd.merge(
        df_ant,
        df_act,
        on=claus,
        how="outer",
        suffixes=("_anterior", "_actual"),
        indicator=True
    )

    files = []

    for _, row in df.iterrows():
        empresa = f'{row["Empresa"]} - {row["Nom empresa"]}'
        codi = row["Codi treballador"]

        treballador = row.get("Treballador_actual")
        if pd.isna(treballador) or treballador == "":
            treballador = row.get("Treballador_anterior", "")

        dev_ant = row.get("TOTAL DEVENGOS_anterior", 0)
        dev_act = row.get("TOTAL DEVENGOS_actual", 0)
        ret_ant = row.get("TOTAL RETENCION_anterior", 0)
        ret_act = row.get("TOTAL RETENCION_actual", 0)
        liq_ant = row.get("TOTAL LIQUIDO_anterior", 0)
        liq_act = row.get("TOTAL LIQUIDO_actual", 0)
        tot_ant = row.get("TOTAL_anterior", 0)
        tot_act = row.get("TOTAL_actual", 0)

        valors = [dev_ant, dev_act, ret_ant, ret_act, liq_ant, liq_act, tot_ant, tot_act]
        valors = [0 if pd.isna(v) else v for v in valors]

        dev_ant, dev_act, ret_ant, ret_act, liq_ant, liq_act, tot_ant, tot_act = valors

        if row["_merge"] == "right_only":
            incidencia = "TREBALLADOR NOU"
        elif row["_merge"] == "left_only":
            incidencia = "NO APAREIX AL MES ACTUAL"
        else:
            incidencia = "DIFERÈNCIA"

        hi_ha_diferencia = (
            abs(dev_act - dev_ant) > 0.01 or
            abs(ret_act - ret_ant) > 0.01 or
            abs(liq_act - liq_ant) > 0.01 or
            abs(tot_act - tot_ant) > 0.01
        )

        if row["_merge"] != "both" or hi_ha_diferencia:
            files.append({
                "Empresa": empresa,
                "Codi treballador": codi,
                "Treballador": treballador,
                "Incidència": incidencia,
                "Devengos": format_comparacio(dev_ant, dev_act),
                "Retenció": format_comparacio(ret_ant, ret_act),
                "Líquid": format_comparacio(liq_ant, liq_act),
                "Total": format_comparacio(tot_ant, tot_act),
            })

    resultat = pd.DataFrame(files)

    if resultat.empty:
        return resultat

    resultat = resultat.sort_values(
        by=["Empresa", "Codi treballador"]
    )

    return resultat


def exportar_excel(df):
    output = BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Informe diferencies")

        workbook = writer.book
        worksheet = writer.sheets["Informe diferencies"]

        for column_cells in worksheet.columns:
            max_length = 0
            column_letter = column_cells[0].column_letter

            for cell in column_cells:
                try:
                    max_length = max(max_length, len(str(cell.value)))
                except:
                    pass

            worksheet.column_dimensions[column_letter].width = min(max_length + 3, 45)

    output.seek(0)
    return output


if pdf_mes_anterior and pdf_mes_actual:
    st.info("Llegint i comparant PDFs...")

    text_anterior = llegir_pdf(pdf_mes_anterior)
    text_actual = llegir_pdf(pdf_mes_actual)

    df_anterior = processar_pdf(text_anterior)
    df_actual = processar_pdf(text_actual)

    informe = comparar(df_anterior, df_actual)

    if informe.empty:
        st.success("No s'han detectat diferències.")
    else:
        st.subheader("Informe de diferències")
        st.write(f"Treballadors amb incidències: {len(informe)}")
        st.dataframe(informe, use_container_width=True)

        st.download_button(
            label="Descarregar Excel",
            data=exportar_excel(informe),
            file_name="informe_diferencies_nomines.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
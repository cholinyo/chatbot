# DocID presence — colección `onda_docs_minilm` (k=20, probe_k=200, n=50)

- Encontrados en top-k: **FAISS 4 / 50**, **Chroma 4 / 50**
- Encontrados en probe_k: **FAISS 8 / 50**, **Chroma 6 / 50**
- Gap medio (rank - k) cuando está fuera de k: **FAISS 80.0**, **Chroma 16.0**

idx | docid | query | FAISS rank@k | FAISS rank@probe | gap | Chroma rank@k | Chroma rank@probe | gap
---:|---:|---|---:|---:|---:|---:|---:|---:
1 | 1661 | https://videoacta.onda.es/viafirma/conectorAutenticacionOpenId?openid.identity=https%3A%2F%2Fvideoacta.onda.es%2Fviafirma%2Fpip%2F39B9937866DE043F5BFB1A461771BB21&openid.return_to=https%3A%2F%2Fvideoacta.onda.es%2Fviafirma%2FtestAuthentication%3Fopenid.rpnonce%3D2025-09-05T01%253A10%253A15Z0%26openid.rpsig%3DxGjvCE9F0kiHJzEL%252BRsnHD72%252BG66Ia22nmfuru9Mrjc%253D&openid.trust_root=https%3A%2F%2Fvideoacta.onda.es%2Fviafirma%2FtestAuthentication&openid.assoc_handle=1754839189746-0&openid.mode=checkid_setup&openid.ns.ext1=http%3A%2F%2Fopenid.net%2Fsrv%2Fax%2F1.0&openid.ext1.mode=fetch_request&openid.ext1.type.email=http%3A%2F%2Fschema.openid.net%2Fcontact%2Femail&openid.ext1.type.firstName=http%3A%2F%2Fopenid.net%2Fschema%2FnamePerson%2Ffirst&openid.ext1.type.lastName=http%3A%2F%2Fopenid.net%2Fschema%2FnamePerson%2Flast&openid.ext1.type.numberUserId=http%3A%2F%2Fwww.viavansi.com%2Fschema%2Fperson%2FnumberId&openid.ext1.type.caName=http%3A%2F%2Fwww.viavansi.com%2Fschema%2Fcertificate%2FcaName&openid.ext1.type.oids=http%3A%2F%2Fwww.viavansi.com%2Fschema%2Fcertificate%2Foid&openid.ext1.type.typeCertificate=http%3A%2F%2Fwww.viavansi.com%2Fschema%2Fcertificate%2Ftype&openid.ext1.required=email%2CfirstName%2ClastName%2CnumberUserId%2CcaName%2Coids%2CtypeCertificate | - | - | - | - | - | -
2 | 69 | ACFrOgBjKyjXOvqikvNzAa4vflYqDesg33cfdyzITq51eP_XFYMcfb12bmvoKg9zEk6oBf0ZqnM1w3pH23NhkPVN_6QcQ9vrtmsrfC-Tb2wqeLUXpZj58a4H41G9oUFkezmrcRgUI78D2UgnvkePbKX0HLRUd21m444TXiagZg==.pdf | - | - | - | - | - | -
3 | 71 | ACFrOgDJJmhdbkDbkajQdssK3daQXkfhC0YyEfdtJ14011fx2qMGeqAW_iTSiYlyzZY6ugfGNm-_QjtVtqtLqht36xadipiuNSGQ9_hCmfLpWILtKfuvHsM0favuUEZtjfIXs6frDIHsz1Kpm7ckQTPp5lD-5_a3Ug63mUTuPQ==.pdf | - | - | - | - | - | -
4 | 70 | ACFrOgD8MY-F-rQUwJ50A2b1HHGa4gSwyr74d1M9cUpHQd6nRevm04tbuQqmBI3oW3rf8XmrCw9dcpIxInImaSTXL64H8C2jahxwtRL65NP95rHdj1rg43G0YPsi8d4t22BsshUz_UpR7vgPoZ63.pdf | - | - | - | - | - | -
5 | 205 | DECRETO_DE_AUTORIZACIONES_RELACIONADAS_CON_EL_SISTEMA_DE_ACCESO_A_LOS_FICHEROS_MOVE_Y_PADRON_FACILITADOS_POR_LA_DGT_PARA_LA_GESTION_DEL_IVTM.pdf | 1 | 1 | - | 1 | 1 | -
6 | 647 | https://www.ondaturismo.es/onda/Web_php/index.php?contenido=subapartados_coconut&id_boto=272&title=rea-de-autocaravanas | - | - | - | - | - | -
7 | 310 | Ley 62020, de 11 de noviembre, reguladora de determinados aspectos de los servicios electrónicos de confianza.pdf | 2 | 2 | - | 2 | 2 | -
8 | 1301 | https://transparencia.onda.es/ca/cargos-electos-y-personas-que-ejercen-la-maxima-responsabilidad-de-las-entidades | - | - | - | - | - | -
9 | 1268 | https://participacio.onda.es/remodelaci%C3%B3n-de-las-escaleras-de-las-c/-doctor-g%C3%B3mez-ferrer-y-turia | - | - | - | - | - | -
10 | 304 | Formulario-Acceso-General-Plataforma-de-Intermediacion-de-Datos-ClienteSCSP-CLOUD-doc-20220412_signed.pdf | 12 | 12 | - | 11 | 13 | -
11 | 1269 | https://participacio.onda.es/proceso-participativo-para-atraer-fondos-europeos-en-materia-de-turismo | - | - | - | - | - | -
12 | 272 | DOCUMENTO DE RESPONSABILIDAD POR ARRENDAMIENTO DE LOCAL PARA CASAL DE UNA PEÃ_A (menores) (1).docx | - | 186 | 166 | - | - | -
13 | 319 | Modelo Autorizacion de la solicitud a otro agente (Colegio Monteblanco Onda)_F_signerdAlcaldesa.pdf | - | - | - | - | - | -
14 | 303 | Formulario-Acceso-General-Plataforma-de-Intermediacion-de-Datos-ClienteSCSP-CLOUD-doc-20220412.pdf | 2 | 2 | - | 1 | 1 | -
15 | 27 | 1._Memoria_solar_fotovoltaica_autoconsumo+colectivo_IVAUTF (COLEGIO MONTEBLANCO ONDA)_def+ayc.pdf | - | - | - | - | - | -
16 | 273 | DOCUMENTO DE RESPONSABILIDAD POR ARRENDAMIENTO DE LOCAL PARA CASAL DE UNA PEÃ_A (menores).docx | - | 143 | 123 | - | - | -
17 | 320 | Modelo Autorizacion de la solicitud a otro agente (Museo del Azulejo Onda) _signedAlcaldesa.pdf | - | - | - | - | - | -
18 | 321 | Modelo Autorizacion de la solicitud a otro agente (Pabellón Municipal Onda)_signedAlcaldesa.pdf | - | - | - | - | - | -
19 | 450 | Onda Pabellón_ACUERDO UNIFICACION CONSUMOS GENERACION CONECTADA RED INTERIOR CONSUMO (2).pdf | - | 47 | 27 | - | 48 | 28
20 | 444 | Onda Colegio_ACUERDO UNIFICACION CONSUMOS GENERACION CONECTADA RED INTERIOR CONSUMO (2).pdf | - | 24 | 4 | - | 24 | 4
21 | 477 | Programa_Proyectos_Ciudad_Laboratorio_2025_Anexo_V_Memoria_de_solicitud_signed.pdf (1).pdf | - | - | - | - | - | -
22 | 971 | https://www.onda.es/ond/web_php/index.php?contenido=subapartados_woden&id_boto=534&lang=10 | - | - | - | - | - | -
23 | 973 | https://www.onda.es/ond/web_php/index.php?contenido=subapartados_woden&id_boto=493&lang=10 | - | - | - | - | - | -
24 | 975 | https://www.onda.es/ond/web_php/index.php?contenido=subapartados_woden&id_boto=545&lang=10 | - | - | - | - | - | -
25 | 977 | https://www.onda.es/ond/web_php/index.php?contenido=subapartados_woden&id_boto=136&lang=10 | - | - | - | - | - | -
26 | 979 | https://www.onda.es/ond/web_php/index.php?contenido=subapartados_woden&id_boto=321&lang=10 | - | - | - | - | - | -
27 | 1002 | https://www.onda.es/ond/web_php/index.php?contenido=subapartados_woden&id_boto=556&lang=10 | - | - | - | - | - | -
28 | 1007 | https://www.onda.es/ond/web_php/index.php?contenido=subapartados_woden&id_boto=532&lang=10 | - | - | - | - | - | -
29 | 1014 | https://www.onda.es/ond/web_php/index.php?contenido=subapartados_woden&id_boto=617&lang=10 | - | - | - | - | - | -
30 | 1018 | https://www.onda.es/ond/web_php/index.php?contenido=subapartados_woden&id_boto=533&lang=10 | - | - | - | - | - | -
31 | 1020 | https://www.onda.es/ond/web_php/index.php?contenido=subapartados_woden&id_boto=487&lang=10 | - | - | - | - | - | -
32 | 1022 | https://www.onda.es/ond/web_php/index.php?contenido=subapartados_woden&id_boto=143&lang=10 | - | - | - | - | - | -
33 | 1024 | https://www.onda.es/ond/web_php/index.php?contenido=subapartados_woden&id_boto=622&lang=10 | - | - | - | - | - | -
34 | 1026 | https://www.onda.es/ond/web_php/index.php?contenido=subapartados_woden&id_boto=319&lang=10 | - | - | - | - | - | -
35 | 1029 | https://www.onda.es/ond/web_php/index.php?contenido=subapartados_woden&id_boto=331&lang=10 | - | - | - | - | - | -
36 | 1031 | https://www.onda.es/ond/web_php/index.php?contenido=subapartados_woden&id_boto=144&lang=10 | - | - | - | - | - | -
37 | 1033 | https://www.onda.es/ond/web_php/index.php?contenido=subapartados_woden&id_boto=179&lang=10 | - | - | - | - | - | -
38 | 1035 | https://www.onda.es/ond/web_php/index.php?contenido=subapartados_woden&id_boto=157&lang=10 | - | - | - | - | - | -
39 | 1037 | https://www.onda.es/ond/web_php/index.php?contenido=subapartados_woden&id_boto=149&lang=10 | - | - | - | - | - | -
40 | 1046 | https://www.onda.es/ond/web_php/index.php?contenido=subapartados_woden&id_boto=151&lang=10 | - | - | - | - | - | -
41 | 1048 | https://www.onda.es/ond/web_php/index.php?contenido=subapartados_woden&id_boto=148&lang=10 | - | - | - | - | - | -
42 | 1050 | https://www.onda.es/ond/web_php/index.php?contenido=subapartados_woden&id_boto=159&lang=10 | - | - | - | - | - | -
43 | 1058 | https://www.onda.es/ond/web_php/index.php?contenido=subapartados_woden&id_boto=320&lang=10 | - | - | - | - | - | -
44 | 1062 | https://www.onda.es/ond/web_php/index.php?contenido=subapartados_woden&id_boto=153&lang=10 | - | - | - | - | - | -
45 | 1065 | https://www.onda.es/ond/web_php/index.php?contenido=subapartados_woden&id_boto=330&lang=10 | - | - | - | - | - | -
46 | 1067 | https://www.onda.es/ond/web_php/index.php?contenido=subapartados_woden&id_boto=152&lang=10 | - | - | - | - | - | -
47 | 1070 | https://www.onda.es/ond/web_php/index.php?contenido=subapartados_woden&id_boto=338&lang=10 | - | - | - | - | - | -
48 | 1072 | https://www.onda.es/ond/web_php/index.php?contenido=subapartados_woden&id_boto=154&lang=10 | - | - | - | - | - | -
49 | 1074 | https://www.onda.es/ond/web_php/index.php?contenido=subapartados_woden&id_boto=155&lang=10 | - | - | - | - | - | -
50 | 1076 | https://www.onda.es/ond/web_php/index.php?contenido=subapartados_woden&id_boto=160&lang=10 | - | - | - | - | - | -

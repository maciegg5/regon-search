# api/regon/__init__.py
import azure.functions as func
import json
import requests
from lxml import etree
import re
import os

API_URL = "https://wyszukiwarkaregon.stat.gov.pl/wsBIR/UslugaBIRzewnPubl.svc"
API_KEY = os.environ.get('REGON_API_KEY', 'c38c77059648411cb578')

def soap_envelope(body, action):
    return f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope" 
               xmlns:ns="http://CIS/BIR/PUBL/2014/07"
               xmlns:dat="http://CIS/BIR/PUBL/2014/07/DataContract">
    <soap:Header xmlns:wsa="http://www.w3.org/2005/08/addressing">
        <wsa:To>{API_URL}</wsa:To>
        <wsa:Action>http://CIS/BIR/PUBL/2014/07/IUslugaBIRzewnPubl/{action}</wsa:Action>
    </soap:Header>
    <soap:Body>
        {body}
    </soap:Body>
</soap:Envelope>"""

def extract_xml_from_mtom(response_text):
    """Wyciąga XML z odpowiedzi MTOM (multipart)"""
    match = re.search(r'(<s:Envelope.*?</s:Envelope>)', response_text, re.DOTALL)
    if match:
        return match.group(1)
    match = re.search(r'(<soap:Envelope.*?</soap:Envelope>)', response_text, re.DOTALL)
    if match:
        return match.group(1)
    return response_text

def zaloguj():
    body = f"<ns:Zaloguj><ns:pKluczUzytkownika>{API_KEY}</ns:pKluczUzytkownika></ns:Zaloguj>"
    xml = soap_envelope(body, "Zaloguj")
    
    response = requests.post(
        API_URL,
        data=xml.encode("utf-8"),
        headers={
            "Content-Type": "application/soap+xml; charset=utf-8",
            "SOAPAction": "http://CIS/BIR/PUBL/2014/07/IUslugaBIRzewnPubl/Zaloguj"
        }
    )
    
    if response.status_code != 200:
        raise Exception(f"Błąd HTTP: {response.status_code}")
    
    xml_content = extract_xml_from_mtom(response.text)
    root = etree.fromstring(xml_content.encode('utf-8'))
    sid = root.xpath("//*[local-name()='ZalogujResult']/text()")
    
    if not sid or not sid[0].strip():
        raise Exception("Nie udało się wyciągnąć SID z odpowiedzi.")
    
    return sid[0]

def szukaj_po_nip(nip, sid):
    body = f"""<ns:DaneSzukajPodmioty>
        <ns:pParametryWyszukiwania>
            <dat:Nip>{nip}</dat:Nip>
        </ns:pParametryWyszukiwania>
    </ns:DaneSzukajPodmioty>"""
    
    xml = soap_envelope(body, "DaneSzukajPodmioty")
    
    response = requests.post(
        API_URL,
        data=xml.encode("utf-8"),
        headers={
            "Content-Type": "application/soap+xml; charset=utf-8",
            "SOAPAction": "http://CIS/BIR/PUBL/2014/07/IUslugaBIRzewnPubl/DaneSzukajPodmioty",
            "sid": sid
        }
    )
    
    if response.status_code != 200:
        return None
    
    xml_content = extract_xml_from_mtom(response.text)
    root = etree.fromstring(xml_content.encode('utf-8'))
    result = root.xpath("//*[local-name()='DaneSzukajPodmiotyResult']/text()")
    
    if result and result[0].strip():
        return result[0]
    return None

def pobierz_pelny_raport(regon, sid, typ_podmiotu):
    if typ_podmiotu == 'P':
        nazwa_raportu = 'PublDaneRaportPrawna'
    elif typ_podmiotu == 'F':
        nazwa_raportu = 'PublDaneRaportDzialalnosciFizycznej'
    else:
        nazwa_raportu = 'PublDaneRaportPrawna'
    
    body = f"""<ns:DanePobierzPelnyRaport>
        <ns:pRegon>{regon}</ns:pRegon>
        <ns:pNazwaRaportu>{nazwa_raportu}</ns:pNazwaRaportu>
    </ns:DanePobierzPelnyRaport>"""
    
    xml = soap_envelope(body, "DanePobierzPelnyRaport")
    
    response = requests.post(
        API_URL,
        data=xml.encode("utf-8"),
        headers={
            "Content-Type": "application/soap+xml; charset=utf-8",
            "SOAPAction": "http://CIS/BIR/PUBL/2014/07/IUslugaBIRzewnPubl/DanePobierzPelnyRaport",
            "sid": sid
        }
    )
    
    if response.status_code != 200:
        return None
    
    try:
        xml_content = extract_xml_from_mtom(response.text)
        root = etree.fromstring(xml_content.encode('utf-8'))
        result = root.xpath("//*[local-name()='DanePobierzPelnyRaportResult']/text()")
        
        if result and result[0].strip():
            return result[0]
        return None
    except:
        return None

def parse_xml_to_dict(xml_string):
    """Konwertuje XML na słownik"""
    try:
        root = etree.fromstring(xml_string.encode('utf-8'))
        result = {}
        for elem in root.iter():
            if elem.text and elem.text.strip() and elem.tag not in ['dane', 'root']:
                result[elem.tag] = elem.text
        return result
    except:
        return {}

def parse_pkd_list(xml_string):
    """Parsuje listę kodów PKD"""
    try:
        root = etree.fromstring(xml_string.encode('utf-8'))
        pkd_list = []
        
        # Szukamy wszystkich elementów z kodem PKD
        for dane in root.findall('.//{http://CIS/BIR/PUBL/2014/07}dane'):
            pkd = {}
            for child in dane:
                tag = child.tag.replace('{http://CIS/BIR/PUBL/2014/07}', '')
                if child.text:
                    pkd[tag] = child.text
            if pkd:
                pkd_list.append(pkd)
        
        return pkd_list
    except:
        return []

def main(req: func.HttpRequest) -> func.HttpResponse:
    try:
        # Pobierz NIP z requestu
        req_body = req.get_json()
        nip = req_body.get('nip', '').replace('-', '').strip()
        
        if not nip or len(nip) != 10 or not nip.isdigit():
            return func.HttpResponse(
                json.dumps({'error': 'Nieprawidłowy format NIP'}),
                mimetype="application/json",
                status_code=400
            )
        
        # Logowanie
        sid = zaloguj()
        
        # Wyszukaj podmiot
        dane = szukaj_po_nip(nip, sid)
        
        if not dane:
            return func.HttpResponse(
                json.dumps({'error': 'Nie znaleziono podmiotu o podanym NIP'}),
                mimetype="application/json",
                status_code=404
            )
        
        # Parsuj dane podstawowe
        podstawowe = parse_xml_to_dict(dane)
        
        regon = podstawowe.get('Regon')
        typ = podstawowe.get('Typ')
        
        result = {
            'podstawowe': podstawowe,
            'pkd': []
        }
        
        # Pobierz pełny raport jeśli mamy REGON
        if regon:
            pelny_raport = pobierz_pelny_raport(regon, sid, typ)
            if pelny_raport:
                result['pkd'] = parse_pkd_list(pelny_raport)
        
        return func.HttpResponse(
            json.dumps(result, ensure_ascii=False),
            mimetype="application/json",
            status_code=200
        )
        
    except Exception as e:
        return func.HttpResponse(
            json.dumps({'error': f'Błąd serwera: {str(e)}'}),
            mimetype="application/json",
            status_code=500
        )
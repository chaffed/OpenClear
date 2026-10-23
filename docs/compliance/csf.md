# NIST CSF 2.0

*Current as of 2026-10-22. Checked against its sources at least once a year (control check `regulatory_currency`).*

OpenClear's [controls](controls.md) and the [complementary user entity controls](cuecs.md)
(CUECs) by NIST Cybersecurity Framework 2.0 subcategory. Both inventories are filed under
NIST SP 800-53 Rev. 5.1.1 controls. This page follows NIST's own mapping from CSF 2.0 to those
controls (the informative references in the [CSF 2.0 Reference Tool](https://csrc.nist.gov/projects/cprt)), not a judgment
of ours. It isn't legal advice ([Compliance](README.md)).

A subcategory that NIST maps to a control also covers its enhancements (`AC-2` covers
`AC-2(1)`), and one mapped to a whole family (`IR`) covers every control in it. A subcategory
with nothing below isn't one OpenClear's controls or CUECs address.

NIST's mapping links no subcategory to `AU-10`, `MA-4`, `SC-18`, `SC-23`, so a control or CUEC filed only under those isn't on this page.

## Govern (GV)

| Subcategory | OpenClear controls | CUECs |
|---|---|---|
| **GV.OC-01** The organizational mission is understood and informs cybersecurity risk management | — | — |
| **GV.OC-02** Internal and external stakeholders are understood, and their needs and expectations regarding cybersecurity risk management are understood and considered | — | — |
| **GV.OC-03** Legal, regulatory, and contractual requirements regarding cybersecurity - including privacy and civil liberties obligations - are understood and managed | — | — |
| **GV.OC-04** Critical objectives, capabilities, and services that external stakeholders depend on or expect from the organization are understood and communicated | — | — |
| **GV.OC-05** Outcomes, capabilities, and services that the organization depends on are understood and communicated | — | B-SA-9-01 |
| **GV.RM-01** Risk management objectives are established and agreed to by organizational stakeholders | — | — |
| **GV.RM-02** Risk appetite and risk tolerance statements are established, communicated, and maintained | — | — |
| **GV.RM-03** Cybersecurity risk management activities and outcomes are included in enterprise risk management processes | — | — |
| **GV.RM-04** Strategic direction that describes appropriate risk response options is established and communicated | — | — |
| **GV.RM-05** Lines of communication across the organization are established for cybersecurity risks, including risks from suppliers and other third parties | — | — |
| **GV.RM-06** A standardized method for calculating, documenting, categorizing, and prioritizing cybersecurity risks is established and communicated | — | B-RA-3-02, B-RA-3-01 |
| **GV.RM-07** Strategic opportunities (i.e., positive risks) are characterized and are included in organizational cybersecurity risk discussions | — | B-RA-3-02, B-RA-3-01 |
| **GV.RR-01** Organizational leadership is responsible and accountable for cybersecurity risk and fosters a culture that is risk-aware, ethical, and continually improving | — | — |
| **GV.RR-02** Roles, responsibilities, and authorities related to cybersecurity risk management are established, communicated, understood, and enforced | — | — |
| **GV.RR-03** Adequate resources are allocated commensurate with the cybersecurity risk strategy, roles, responsibilities, and policies | — | — |
| **GV.RR-04** Cybersecurity is included in human resources practices | — | — |
| **GV.PO-01** Policy for managing cybersecurity risks is established based on organizational context, cybersecurity strategy, and priorities and is communicated and enforced | — | — |
| **GV.PO-02** Policy for managing cybersecurity risks is reviewed, updated, communicated, and enforced to reflect changes in requirements, threats, technology, and organizational mission | — | — |
| **GV.OV-01** Cybersecurity risk management strategy outcomes are reviewed to inform and adjust strategy and direction | — | — |
| **GV.OV-02** The cybersecurity risk management strategy is reviewed and adjusted to ensure coverage of organizational requirements and risks | — | — |
| **GV.OV-03** Organizational cybersecurity risk management performance is evaluated and reviewed for adjustments needed | — | — |
| **GV.SC-01** A cybersecurity supply chain risk management program, strategy, objectives, policies, and processes are established and agreed to by organizational stakeholders | — | — |
| **GV.SC-02** Cybersecurity roles and responsibilities for suppliers, customers, and partners are established, communicated, and coordinated internally and externally | — | — |
| **GV.SC-03** Cybersecurity supply chain risk management is integrated into cybersecurity and enterprise risk management, risk assessment, and improvement processes | — | B-RA-3-02, B-RA-3-01 |
| **GV.SC-04** Suppliers are known and prioritized by criticality | — | B-SA-9-01 |
| **GV.SC-05** Requirements to address cybersecurity risks in supply chains are established, prioritized, and integrated into contracts and other types of agreements with suppliers and other relevant third parties | — | B-SA-9-01 |
| **GV.SC-06** Planning and due diligence are performed to reduce risks before entering into formal supplier or other third-party relationships | — | B-SA-9-01 |
| **GV.SC-07** The risks posed by a supplier, their products and services, and other third parties are understood, recorded, prioritized, assessed, responded to, and monitored over the course of the relationship | — | B-SA-9-01 |
| **GV.SC-08** Relevant suppliers and other third parties are included in incident planning, response, and recovery activities | — | B-SA-9-01 |
| **GV.SC-09** Supply chain security practices are integrated into cybersecurity and enterprise risk management programs, and their performance is monitored throughout the technology product and service life cycle | — | B-RA-3-02, B-RA-3-01, B-SA-9-01 |
| **GV.SC-10** Cybersecurity supply chain risk management plans include provisions for activities that occur after the conclusion of a partnership or service agreement | — | B-RA-3-02, B-RA-3-01, B-SA-9-01, H-RA-5-01 |

## Identify (ID)

| Subcategory | OpenClear controls | CUECs |
|---|---|---|
| **ID.AM-01** Inventories of hardware managed by the organization are maintained | OC-CM-03 | — |
| **ID.AM-02** Inventories of software, services, and systems managed by the organization are maintained | OC-CM-03 | B-SA-9-01 |
| **ID.AM-03** Representations of the organization's authorized network communication and internal and external network data flows are maintained | OC-AC-03, OC-AC-09, OC-SI-06, OC-SI-08 | B-CA-3-01, B-CA-3-02 |
| **ID.AM-04** Inventories of services provided by suppliers are maintained | — | B-SA-9-01 |
| **ID.AM-05** Assets are prioritized based on classification, criticality, resources, and impact on the mission | — | B-RA-3-02, B-RA-3-01 |
| **ID.AM-07** Inventories of data and corresponding metadata for designated data types are maintained | — | — |
| **ID.AM-08** Systems, hardware, software, services, and data are managed throughout their life cycles | — | — |
| **ID.RA-01** Vulnerabilities in assets are identified, validated, and recorded | OC-AU-04, OC-SI-03, OC-SI-04, OC-SI-05, OC-SI-09, OC-SI-10, OC-CA-01, OC-CA-02, OC-CA-03 | B-RA-3-02, B-RA-3-01, B-CA-7-01, B-CA-7-02, B-SI-4-01, B-SI-4-02, C-SI-4-01, H-RA-5-01, H-SI-4-01 |
| **ID.RA-02** Cyber threat intelligence is received from information sharing forums and sources | — | — |
| **ID.RA-03** Internal and external threats to the organization are identified and recorded | — | B-RA-3-02, B-RA-3-01 |
| **ID.RA-04** Potential impacts and likelihoods of threats exploiting vulnerabilities are identified and recorded | — | B-RA-3-02, B-RA-3-01 |
| **ID.RA-05** Threats, vulnerabilities, likelihoods, and impacts are used to understand inherent risk and inform risk response prioritization | — | B-RA-3-02, B-RA-3-01 |
| **ID.RA-06** Risk responses are chosen, prioritized, planned, tracked, and communicated | — | — |
| **ID.RA-07** Changes and exceptions are managed, assessed for risk impact, recorded, and tracked | OC-AC-07, OC-AC-13, OC-SI-04, OC-CM-02, OC-CA-01, OC-CA-02, OC-CA-03 | B-CM-3-01, B-CM-4-01, B-CA-7-01, B-CA-7-02, C-CM-4-01 |
| **ID.RA-08** Processes for receiving, analyzing, and responding to vulnerability disclosures are established | — | H-RA-5-01 |
| **ID.RA-09** The authenticity and integrity of hardware and software are assessed prior to acquisition and use | OC-AU-02, OC-SC-03, OC-SI-02 | B-SI-7-01, C-SI-7-01 |
| **ID.RA-10** Critical suppliers are assessed prior to acquisition | — | — |
| **ID.IM-01** Improvements are identified from evaluations | OC-AU-04, OC-SI-03, OC-SI-04, OC-SI-05, OC-SI-09, OC-SI-10, OC-CM-02, OC-CA-01, OC-CA-02, OC-CA-03 | B-RA-3-02, B-RA-3-01, B-CA-7-01, B-CA-7-02, B-SI-4-01, B-SI-4-02, B-CP-2-02, B-CP-2-01, C-SI-4-01, H-SI-2-01, H-RA-5-01, H-SI-4-01 |
| **ID.IM-02** Improvements are identified from security tests and exercises, including those done in coordination with suppliers and relevant third parties | OC-AU-04, OC-SI-03, OC-SI-04, OC-SI-05, OC-SI-09, OC-SI-10, OC-CM-02, OC-CA-01, OC-CA-02, OC-CA-03 | B-RA-3-02, B-RA-3-01, B-CA-7-01, B-CA-7-02, B-SI-4-01, B-SI-4-02, B-CP-2-02, B-CP-2-01, C-SI-4-01, H-SI-2-01, H-RA-5-01, H-SI-4-01 |
| **ID.IM-03** Improvements are identified from execution of operational processes, procedures, and activities | OC-AU-04, OC-SI-03, OC-SI-04, OC-SI-05, OC-SI-09, OC-SI-10, OC-CM-02, OC-CA-01, OC-CA-02, OC-CA-03 | B-RA-3-02, B-RA-3-01, B-CA-7-01, B-CA-7-02, B-SI-4-01, B-SI-4-02, B-CP-2-02, B-CP-2-01, C-SI-4-01, H-SI-2-01, H-RA-5-01, H-SI-4-01 |
| **ID.IM-04** Incident response plans and other cybersecurity plans that affect operations are established, communicated, maintained, and improved | OC-SI-05 | B-CP-2-02, B-CP-2-01 |

## Protect (PR)

| Subcategory | OpenClear controls | CUECs |
|---|---|---|
| **PR.AA-01** Identities and credentials for authorized users, services, and hardware are managed by the organization | OC-AC-02, OC-IA-01, OC-IA-02, OC-IA-03, OC-IA-04, OC-IA-05 | B-AC-2-01, B-AC-2(1)-01, B-IA-2(1)-01, B-IA-2(2)-01, B-IA-2(2)-02, B-IA-2(2)-03, B-IA-5(1)-01, B-IA-5-01, B-AC-2-02, B-IA-8-01, C-AC-2-01, C-AC-2-02, C-AC-2(1)-01, C-IA-2-01, C-IA-5-01 |
| **PR.AA-02** Identities are proofed and bound to credentials based on the context of interactions | — | B-IA-12-01 |
| **PR.AA-03** Users, services, and hardware are authenticated | OC-AC-10, OC-IA-01, OC-IA-02, OC-IA-03, OC-IA-04, OC-IA-05, OC-SC-06 | B-IA-2(1)-01, B-IA-2(2)-01, B-IA-2(2)-02, B-IA-2(2)-03, B-IA-5(1)-01, B-IA-5-01, B-IA-8-01, C-IA-2-01, C-IA-5-01 |
| **PR.AA-04** Identity assertions are protected, conveyed, and verified | — | — |
| **PR.AA-05** Access permissions, entitlements, and authorizations are defined in a policy, managed, enforced, and reviewed, and incorporate the principles of least privilege and separation of duties | OC-AC-01, OC-AC-02, OC-AC-03, OC-AC-04, OC-AC-05, OC-AC-06, OC-AC-07, OC-AC-08, OC-AC-09, OC-AC-12, OC-AC-13, OC-IA-03, OC-SI-08, OC-SI-09 | B-AC-2-01, B-AC-2(1)-01, B-AC-6(7)-01, B-AC-6-01, B-AC-5-01, B-AC-3(2)-01, B-AC-2-02, C-AC-2-01, C-AC-2-02, C-AC-2(1)-01, C-AC-6(7)-01, C-AC-5-01, C-AC-3-01, H-AC-6(5)-01 |
| **PR.AA-06** Physical access to assets is managed, monitored, and enforced commensurate with risk | — | — |
| **PR.AT-01** Personnel are provided with awareness and training so that they possess the knowledge and skills to perform general tasks with cybersecurity risks in mind | — | B-AT-3-01, C-AT-2(3)-01 |
| **PR.AT-02** Individuals in specialized roles are provided with awareness and training so that they possess the knowledge and skills to perform relevant tasks with cybersecurity risks in mind | — | B-AT-3-01 |
| **PR.DS-01** The confidentiality, integrity, and availability of data-at-rest are protected | OC-AU-02, OC-AU-04, OC-SC-01, OC-SC-02, OC-SC-03, OC-SC-05, OC-SC-07, OC-SI-02, OC-SI-03, OC-SI-05, OC-SI-06, OC-SI-08, OC-SI-09, OC-SI-10, OC-CA-02 | B-SI-4-01, B-SI-4-02, B-SI-3-01, B-CA-3-01, B-SI-7-01, B-CA-3-02, C-SI-7-01, C-SI-4-01, C-SC-28-01, H-SC-28(1)-01, H-SC-12-01, H-SC-7-01, H-CP-9-01, H-SI-4-01 |
| **PR.DS-02** The confidentiality, integrity, and availability of data-in-transit are protected | OC-AU-02, OC-AU-04, OC-SC-01, OC-SC-03, OC-SC-04, OC-SC-05, OC-SC-07, OC-SI-02, OC-SI-03, OC-SI-05, OC-SI-06, OC-SI-08, OC-SI-09, OC-SI-10, OC-CA-02 | B-SI-4-01, B-SI-4-02, B-SI-3-01, B-CA-3-01, B-SI-7-01, B-CA-3-02, B-SC-8-01, C-SI-7-01, C-SI-4-01, H-SC-12-01, H-SC-8(1)-01, H-SC-7-01, H-SI-4-01 |
| **PR.DS-10** The confidentiality, integrity, and availability of data-in-use are protected | OC-AC-01, OC-AC-02, OC-AC-03, OC-AC-04, OC-AC-06, OC-AC-07, OC-AC-08, OC-AC-09, OC-AC-12, OC-AC-13, OC-IA-03, OC-AU-01, OC-AU-02, OC-AU-03, OC-AU-04, OC-SC-01, OC-SC-03, OC-SC-05, OC-SC-07, OC-SI-01, OC-SI-02, OC-SI-03, OC-SI-05, OC-SI-06, OC-SI-08, OC-SI-09, OC-SI-10, OC-CA-02 | B-AC-2-01, B-AC-2(1)-01, B-AC-3(2)-01, B-AC-2-02, B-SI-4-01, B-SI-4-02, B-SI-3-01, B-CA-3-01, B-SI-7-01, B-CA-3-02, C-AC-2-01, C-AC-2-02, C-AC-2(1)-01, C-SI-7-01, C-SI-10-01, C-SI-4-01, C-AC-3-01, H-SC-7-01, H-CP-9-01, H-AU-9(2)-01, H-SI-4-01 |
| **PR.DS-11** Backups of data are created, protected, maintained, and tested | — | H-CP-9-01 |
| **PR.PS-01** Configuration management practices are established and applied | OC-AC-07, OC-AC-13, OC-SI-04, OC-CM-01, OC-CM-02, OC-CM-03 | B-CM-3-01, B-CM-4-01, C-CM-4-01, H-CM-6-01 |
| **PR.PS-02** Software is maintained, replaced, and removed commensurate with risk | OC-AU-02, OC-SC-03, OC-SI-02, OC-CM-02 | B-SI-7-01, C-SI-7-01, H-SI-2-01 |
| **PR.PS-03** Hardware is maintained, replaced, and removed commensurate with risk | — | — |
| **PR.PS-04** Log records are generated and made available for continuous monitoring | OC-AU-01, OC-AU-03, OC-AU-04, OC-AU-05, OC-SI-10 | B-AU-6-01, B-AU-11-01 |
| **PR.PS-05** Installation and execution of unauthorized software are prevented | — | — |
| **PR.PS-06** Secure software development practices are integrated, and their performance is monitored throughout the software development life cycle | — | — |
| **PR.IR-01** Networks and environments are protected from unauthorized logical access and usage | OC-AC-01, OC-AC-03, OC-AC-04, OC-AC-06, OC-AC-07, OC-AC-08, OC-AC-09, OC-AC-12, OC-AC-13, OC-SC-05, OC-SC-06, OC-SC-07, OC-SI-08 | B-AC-3(2)-01, C-AC-3-01, H-SC-7-01 |
| **PR.IR-02** The organization's technology assets are protected from environmental threats | OC-SI-05 | B-CP-2-02, B-CP-2-01 |
| **PR.IR-03** Mechanisms are implemented to achieve resilience requirements in normal and adverse situations | OC-SI-05, OC-SI-07, OC-CM-02 | B-IR-6-03, B-IR-6-04, B-IR-6-02, B-CP-2-02, B-CP-2-01, B-IR-6-01, C-IR-6-01, H-CP-9-01, H-IR-6-01 |
| **PR.IR-04** Adequate resource capacity to ensure availability is maintained | — | — |

## Detect (DE)

| Subcategory | OpenClear controls | CUECs |
|---|---|---|
| **DE.CM-01** Networks and network services are monitored to find potentially adverse events | OC-AC-02, OC-AC-07, OC-AC-13, OC-IA-03, OC-AU-01, OC-AU-04, OC-SC-05, OC-SC-06, OC-SC-07, OC-SI-03, OC-SI-05, OC-SI-09, OC-SI-10, OC-CM-02, OC-CA-01, OC-CA-02, OC-CA-03 | B-AC-2-01, B-AC-2(1)-01, B-CM-3-01, B-AC-2-02, B-CA-7-01, B-CA-7-02, B-SI-4-01, B-SI-4-02, C-AC-2-01, C-AC-2-02, C-AC-2(1)-01, C-SI-4-01, H-SC-7-01, H-SI-4-01 |
| **DE.CM-02** The physical environment is monitored to find potentially adverse events | OC-CA-01, OC-CA-02, OC-CA-03 | B-CA-7-01, B-CA-7-02 |
| **DE.CM-03** Personnel activity and technology usage are monitored to find potentially adverse events | OC-AC-02, OC-IA-03, OC-AU-01, OC-AU-04, OC-CA-01, OC-CA-02, OC-CA-03 | B-AC-2-01, B-AC-2(1)-01, B-AC-2-02, B-CA-7-01, B-CA-7-02, C-AC-2-01, C-AC-2-02, C-AC-2(1)-01 |
| **DE.CM-06** External service provider activities and services are monitored to find potentially adverse events | OC-AU-04, OC-SI-03, OC-SI-05, OC-SI-09, OC-SI-10, OC-CA-01, OC-CA-02, OC-CA-03 | B-CA-7-01, B-CA-7-02, B-SI-4-01, B-SI-4-02, B-SA-9-01, C-SI-4-01, H-SI-4-01 |
| **DE.CM-09** Computing hardware and software, runtime environments, and their data are monitored to find potentially adverse events | OC-AC-03, OC-AC-07, OC-AC-09, OC-AC-13, OC-AU-01, OC-AU-02, OC-AU-04, OC-SC-03, OC-SI-02, OC-SI-03, OC-SI-05, OC-SI-09, OC-SI-10, OC-CM-01, OC-CM-02, OC-CA-01, OC-CA-02, OC-CA-03 | B-CM-3-01, B-CA-7-01, B-CA-7-02, B-SI-4-01, B-SI-4-02, B-SI-7-01, C-SI-7-01, C-SI-4-01, H-CM-6-01, H-SI-4-01 |
| **DE.AE-02** Potentially adverse events are analyzed to better understand associated activities | OC-AU-04, OC-SI-03, OC-SI-05, OC-SI-09, OC-SI-10, OC-CA-01, OC-CA-02, OC-CA-03 | B-CA-7-01, B-CA-7-02, B-AU-6-01, B-SI-4-01, B-SI-4-02, C-SI-4-01, H-SI-4-01 |
| **DE.AE-03** Information is correlated from multiple sources | OC-AU-04, OC-SI-03, OC-SI-05, OC-SI-09, OC-SI-10, OC-CA-01, OC-CA-02, OC-CA-03 | B-CA-7-01, B-CA-7-02, B-AU-6-01, B-SI-4-01, B-SI-4-02, C-SI-4-01, H-SI-4-01 |
| **DE.AE-04** The estimated impact and scope of adverse events are understood | — | — |
| **DE.AE-06** Information on adverse events is provided to authorized staff and tools | — | B-RA-3-02, B-RA-3-01 |
| **DE.AE-07** Cyber threat intelligence and other contextual information are integrated into the analysis | — | B-RA-3-02, B-RA-3-01 |
| **DE.AE-08** Incidents are declared when adverse events meet the defined incident criteria | — | — |

## Respond (RS)

| Subcategory | OpenClear controls | CUECs |
|---|---|---|
| **RS.MA-01** The incident response plan is executed in coordination with relevant third parties once an incident is declared | OC-SI-07 | B-IR-6-03, B-IR-6-04, B-IR-6-02, B-IR-6-01, C-IR-6-01, H-IR-6-01 |
| **RS.MA-02** Incident reports are triaged and validated | OC-SI-07 | B-IR-6-03, B-IR-6-04, B-IR-6-02, B-IR-6-01, C-IR-6-01, H-IR-6-01 |
| **RS.MA-03** Incidents are categorized and prioritized | OC-SI-07 | B-IR-6-03, B-IR-6-04, B-IR-6-02, B-IR-6-01, C-IR-6-01, H-IR-6-01 |
| **RS.MA-04** Incidents are escalated or elevated as needed | OC-SI-07 | B-IR-6-03, B-IR-6-04, B-IR-6-02, B-IR-6-01, C-IR-6-01, H-IR-6-01 |
| **RS.MA-05** The criteria for initiating incident recovery are applied | — | — |
| **RS.AN-03** Analysis is performed to establish what has taken place during an incident and the root cause of the incident | OC-AU-05 | — |
| **RS.AN-06** Actions performed during an investigation are recorded, and the records' integrity and provenance are preserved | OC-AU-05, OC-SI-07 | B-IR-6-03, B-IR-6-04, B-IR-6-02, B-IR-6-01, C-IR-6-01, H-IR-6-01 |
| **RS.AN-07** Incident data and metadata are collected, and their integrity and provenance are preserved | OC-AU-05, OC-SI-07 | B-IR-6-03, B-IR-6-04, B-IR-6-02, B-IR-6-01, C-IR-6-01, H-IR-6-01 |
| **RS.AN-08** An incident's magnitude is estimated and validated | — | B-RA-3-02, B-RA-3-01 |
| **RS.CO-02** Internal and external stakeholders are notified of incidents | OC-SI-07 | B-IR-6-03, B-IR-6-04, B-IR-6-02, B-IR-6-01, C-IR-6-01, H-IR-6-01 |
| **RS.CO-03** Information is shared with designated internal and external stakeholders | OC-SI-07 | B-IR-6-03, B-IR-6-04, B-IR-6-02, B-IR-6-01, C-IR-6-01, H-IR-6-01 |
| **RS.MI-01** Incidents are contained | — | — |
| **RS.MI-02** Incidents are eradicated | — | — |

## Recover (RC)

| Subcategory | OpenClear controls | CUECs |
|---|---|---|
| **RC.RP-01** The recovery portion of the incident response plan is executed once initiated from the incident response process | OC-CM-02 | — |
| **RC.RP-02** Recovery actions are selected, scoped, prioritized, and performed | OC-CM-02 | — |
| **RC.RP-03** The integrity of backups and other restoration assets is verified before using them for restoration | OC-SI-05 | B-CP-2-02, B-CP-2-01, H-CP-9-01 |
| **RC.RP-04** Critical mission functions and cybersecurity risk management are considered to establish post-incident operational norms | — | — |
| **RC.RP-05** The integrity of restored assets is verified, systems and services are restored, and normal operating status is confirmed | OC-CM-02 | — |
| **RC.RP-06** The end of incident recovery is declared based on criteria, and incident-related documentation is completed | — | — |
| **RC.CO-03** Recovery activities and progress in restoring operational capabilities are communicated to designated internal and external stakeholders | OC-SI-07 | B-IR-6-03, B-IR-6-04, B-IR-6-02, B-IR-6-01, C-IR-6-01, H-IR-6-01 |
| **RC.CO-04** Public updates on incident recovery are shared using approved methods and messaging | OC-SI-05 | B-CP-2-02, B-CP-2-01 |
